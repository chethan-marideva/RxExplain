

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from typing import Any

from .config import KB_PATH

# Indian brand-line suffixes that modify a base brand without changing the
# primary active drug we want to explain.
_BRAND_SUFFIXES = (
    "cv", "sp", "sr", "xl", "xr", "cr", "ds", "mf", "od", "lc", "l", "p",
    "forte", "plus", "ct", "kid", "junior", "jr", "h", "am", "at", "n", "b", "m",
    "tz", "oz", "dx", "gel", "hfa", "redimix", "total", "active", "new",
)

_SUFFIX_RE = re.compile(
    r"[-\s]+(" + "|".join(sorted(_BRAND_SUFFIXES, key=len, reverse=True)) + r")\.?$",
    re.IGNORECASE,
)
_TRAILING_NUM_RE = re.compile(r"[-\s]*\d+(?:\.\d+)?\s*$")


@lru_cache(maxsize=1)
def load_kb() -> dict[str, Any]:
    with open(KB_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _brand_index() -> dict[str, str]:
    """Map a lowercase brand or generic alias -> KB key."""
    idx: dict[str, str] = {}
    for key, entry in load_kb()["drugs"].items():
        idx.setdefault(key.lower(), key)
        # the generic display name, and each word-group inside it
        gen = entry["generic"].lower()
        idx.setdefault(gen, key)
        gen_clean = re.sub(r"\(.*?\)", "", gen).strip()
        idx.setdefault(gen_clean, key)
        for part in re.split(r"\+|,", gen_clean):
            part = part.strip()
            if len(part) > 3:
                idx.setdefault(part, key)
        for brand in entry.get("brands", []):
            idx.setdefault(brand.lower(), key)
    return idx


@lru_cache(maxsize=1)
def _searchable() -> tuple[str, ...]:
    return tuple(_brand_index().keys())


def _variants(name: str) -> list[str]:
    """Progressive simplifications of a written drug name, most specific first."""
    base = re.sub(r"\s+", " ", name.strip().lower())
    base = base.strip(" .,-")
    out = [base]

    # drop a trailing strength number: "Pan 40" -> "pan", "Dolo 650" -> "dolo"
    stripped_num = _TRAILING_NUM_RE.sub("", base).strip(" -")
    if stripped_num and stripped_num != base:
        out.append(stripped_num)

    # drop Indian brand-line suffixes, possibly two deep ("Zerodol-SP" -> "zerodol")
    cur = stripped_num or base
    for _ in range(2):
        nxt = _SUFFIX_RE.sub("", cur).strip(" -")
        if not nxt or nxt == cur:
            break
        out.append(nxt)
        cur = nxt

    # first token only, as a last resort ("montek lc tablet" -> "montek")
    first = base.split(" ")[0].strip(" .,-")
    if first and first not in out:
        out.append(first)

    seen: set[str] = set()
    return [v for v in out if v and not (v in seen or seen.add(v))]


def resolve(name: str | None) -> tuple[str, dict[str, Any], str, float] | None:
    """Resolve a written drug name to ``(kb_key, entry, match_type, score)``.

    ``match_type`` is ``exact``, ``suffix`` or ``fuzzy``. Returns ``None`` when
    the name is not in the KB at all, which the SOTA pipeline treats as a signal
    to fall back to live label retrieval.
    """
    if not name:
        return None
    idx = _brand_index()
    drugs = load_kb()["drugs"]

    variants = _variants(name)
    for i, var in enumerate(variants):
        if var in idx:
            key = idx[var]
            kind = "exact" if i == 0 else "suffix"
            return key, drugs[key], kind, 1.0 if i == 0 else 0.9

    # fuzzy, on the least-mangled variant only, to avoid wild matches
    for var in variants[:2]:
        if len(var) < 4:
            continue
        close = difflib.get_close_matches(var, _searchable(), n=1, cutoff=0.84)
        if close:
            key = idx[close[0]]
            score = difflib.SequenceMatcher(None, var, close[0]).ratio()
            return key, drugs[key], "fuzzy", round(score, 3)
    return None


def get(key: str) -> dict[str, Any] | None:
    return load_kb()["drugs"].get(key)


def all_keys() -> list[str]:
    return list(load_kb()["drugs"].keys())


def kb_stats() -> dict[str, int]:
    kb = load_kb()["drugs"]
    return {
        "drugs": len(kb),
        "brand_aliases": sum(len(v.get("brands", [])) for v in kb.values()),
        "high_risk": sum(1 for v in kb.values() if v.get("high_risk")),
        "index_size": len(_brand_index()),
    }
