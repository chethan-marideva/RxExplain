

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests

from .config import CACHE_DIR

OPENFDA_URL = "https://api.fda.gov/drug/label.json"
RXNAV_RXCUI_URL = "https://rxnav.nlm.nih.gov/REST/rxcui.json"

TIMEOUT = 8
RETRIES = 2
RETRY_SLEEP = 1.0


SECTION_LIMIT = 900
SECTIONS = (
    "indications_and_usage",
    "dosage_and_administration",
    "warnings_and_cautions",
    "warnings",
    "boxed_warning",
    "adverse_reactions",
    "drug_interactions",
    "patient_medication_information",
)

_OPENFDA_DIR = CACHE_DIR / "openfda"
_RXNORM_DIR = CACHE_DIR / "rxnorm"
for _d in (_OPENFDA_DIR, _RXNORM_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:80] or "empty"


def _read_cache(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass  # a cache write failure must never break a run


def _get(url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code == 404:
                return None          # openFDA says "no matches" with a 404
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:      # network, timeout, bad JSON
            last = exc
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP * (attempt + 1))
    _ = last
    return None


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SECTION_LIMIT]


def openfda_label(term: str, *, offline: bool = False) -> dict[str, Any] | None:
    """Fetch and trim the openFDA label for a drug name.

    Returns ``{"term", "matched", "rx_or_otc", "sections": {...}}`` or ``None``.
    """
    cache_path = _OPENFDA_DIR / f"{_slug(term)}.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached.get("label")
    if offline:
        return None

    queries = (
        f'openfda.generic_name:"{term}"',
        f'openfda.brand_name:"{term}"',
        f'openfda.substance_name:"{term}"',
    )
    for query in queries:
        data = _get(OPENFDA_URL, {"search": query, "limit": 1})
        results = (data or {}).get("results") or []
        if not results:
            continue
        rec = results[0]
        of = rec.get("openfda", {}) or {}
        sections = {
            key: _clean(" ".join(rec[key]))
            for key in SECTIONS
            if isinstance(rec.get(key), list) and rec.get(key)
        }
        if not sections:
            continue
        label = {
            "term": term,
            "matched_query": query,
            "generic_name": (of.get("generic_name") or [None])[0],
            "brand_name": (of.get("brand_name") or [None])[0],
            "route": (of.get("route") or [None])[0],
            "sections": sections,
        }
        _write_cache(cache_path, {"label": label})
        return label

    _write_cache(cache_path, {"label": None})  # cache the miss too
    return None


def rxnorm_id(term: str, *, offline: bool = False) -> str | None:
    """Return the RxCUI for a drug name, or ``None``."""
    cache_path = _RXNORM_DIR / f"{_slug(term)}.json"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached.get("rxcui")
    if offline:
        return None

    data = _get(RXNAV_RXCUI_URL, {"name": term, "search": 1})
    ids = ((data or {}).get("idGroup") or {}).get("rxnormId") or []
    rxcui = ids[0] if ids else None
    _write_cache(cache_path, {"rxcui": rxcui})
    return rxcui


def search_terms(written_name: str | None, kb_entry: dict[str, Any] | None) -> list[str]:
    """Candidate query strings for a medication, most likely to match first.

    Indian brand names ("Dolo 650") rarely appear in US label data, so the
    generic name from the local KB is tried first.
    """
    out: list[str] = []
    if kb_entry:
        gen = re.sub(r"\(.*?\)", "", kb_entry["generic"]).strip()
        # a combination generic: try the first component on its own too
        parts = [p.strip() for p in gen.split("+") if p.strip()]
        out.append(gen)
        out.extend(parts)
    if written_name:
        base = re.sub(r"\s*\d+(\.\d+)?\s*$", "", written_name).strip()
        out.append(base)
    seen: set[str] = set()
    return [t for t in out if t and len(t) > 2 and not (t.lower() in seen or seen.add(t.lower()))]


def ground_medication(
    written_name: str | None,
    kb_entry: dict[str, Any] | None,
    *,
    offline: bool = False,
) -> dict[str, Any]:
    """Collect all external grounding for one medication."""
    result: dict[str, Any] = {"rxcui": None, "label": None, "queried": []}
    for term in search_terms(written_name, kb_entry):
        result["queried"].append(term)
        if result["rxcui"] is None:
            result["rxcui"] = rxnorm_id(term, offline=offline)
        if result["label"] is None:
            result["label"] = openfda_label(term, offline=offline)
        if result["label"] and result["rxcui"]:
            break
    return result


def label_context(grounding: dict[str, Any], max_chars: int = 1400) -> str:
    """Render retrieved label text as compact prompt context."""
    label = grounding.get("label")
    if not label:
        return ""
    order = (
        "indications_and_usage", "boxed_warning", "warnings_and_cautions",
        "warnings", "drug_interactions", "adverse_reactions",
        "dosage_and_administration", "patient_medication_information",
    )
    parts: list[str] = []
    budget = max_chars
    for key in order:
        text = label["sections"].get(key)
        if not text:
            continue
        chunk = f"{key}: {text}"[:budget]
        parts.append(chunk)
        budget -= len(chunk)
        if budget <= 120:
            break
    if not parts:
        return ""
    head = f"[openFDA label for {label.get('generic_name') or label['term']}]"
    return head + "\n" + "\n".join(parts)


def cache_stats() -> dict[str, int]:
    return {
        "openfda_cached": len(list(_OPENFDA_DIR.glob("*.json"))),
        "rxnorm_cached": len(list(_RXNORM_DIR.glob("*.json"))),
    }
