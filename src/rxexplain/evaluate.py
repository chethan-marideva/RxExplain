

from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import SYSTEMS
from .config import GOLD_PATH, RESULTS_DIR, TARGET_GRADE_HIGH, TARGET_GRADE_LOW
from .llm import LLMClient, LLMError
from .prompts import JUDGE_SYSTEM, JUDGE_USER
from .schema import Explanation, GoldCase
from .systems import run_system

# Raw sig abbreviations that must never survive into patient-facing text.
# Uppercase-only, word-bounded, so ordinary prose is not flagged.
_LEAK_CODES = (
    "OD", "BD", "BID", "TDS", "TID", "QID", "QDS", "HS", "SOS", "PRN", "STAT",
    "AF", "BF", "AC", "PC", "PO", "SC", "IM", "IV", "SL", "LA", "BBF", "OM",
    "ON", "NOCTE", "TAB", "CAP", "SYP", "INJ", "Q6H", "Q8H", "Q12H", "QD",
)


_LEAK_RE = re.compile(
    r"(?<![A-Za-z0-9-])(" + "|".join(_LEAK_CODES) + r")(?![A-Za-z0-9-])"
)
_SLOT_LEAK_RE = re.compile(r"(?<![\d.-])\d\s*-\s*\d\s*-\s*\d(?:\s*-\s*\d)?(?![\d-])")
_DURATION_LEAK_RE = re.compile(r"(?<![\d/])\d+\s*/\s*(?:7|52|12)(?![\d/])")

_HEADINGS = (
    "WHAT THIS PRESCRIPTION IS FOR",
    "YOUR MEDICINES",
    "HOW TO TAKE THEM THROUGH THE DAY",
    "IMPORTANT SAFETY POINTS",
    "WHEN TO CALL YOUR DOCTOR",
    "DISCLAIMER",
)

JUDGE_DIMENSIONS = ("factual_accuracy", "completeness", "simplicity", "safety")


# --------------------------------------------------------------------- gold set
def load_gold(path: Path | None = None, limit: int | None = None) -> list[GoldCase]:
    """Read ``gold_set.jsonl`` into ``GoldCase`` objects."""
    path = path or GOLD_PATH
    if not path.exists():
        raise FileNotFoundError(f"gold set not found at {path}")
    cases: list[GoldCase] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: bad JSON: {exc}") from exc
            cases.append(
                GoldCase(
                    case_id=rec["case_id"],
                    input_text=rec["input_text"],
                    reference=rec["reference"],
                    drugs=list(rec.get("drugs") or []),
                    must_include=list(rec.get("must_include") or []),
                    difficulty=rec.get("difficulty", "medium"),
                    category=rec.get("category", "general"),
                    notes=rec.get("notes", ""),
                )
            )
    if limit:
        cases = cases[:limit]
    return cases


def gold_stats(cases: Sequence[GoldCase]) -> dict[str, Any]:
    by_cat: dict[str, int] = defaultdict(int)
    by_diff: dict[str, int] = defaultdict(int)
    for c in cases:
        by_cat[c.category] += 1
        by_diff[c.difficulty] += 1
    return {
        "cases": len(cases),
        "categories": dict(sorted(by_cat.items())),
        "difficulty": dict(sorted(by_diff.items())),
        "must_include_checks": sum(len(c.must_include) for c in cases),
        "multi_drug_cases": sum(1 for c in cases if len(c.drugs) > 1),
        "mean_reference_words": round(
            statistics.mean(len(c.reference.split()) for c in cases), 1
        ),
    }


# ------------------------------------------------------------ lexical overlap
_rouge_scorer: Any | None = None


def _rouge(candidate: str, reference: str) -> dict[str, float]:
    global _rouge_scorer
    if _rouge_scorer is None:
        from rouge_score import rouge_scorer as rs

        _rouge_scorer = rs.RougeScorer(
            ["rouge1", "rouge2", "rougeL"], use_stemmer=True
        )
    scores = _rouge_scorer.score(reference, candidate)
    out: dict[str, float] = {}
    for key, val in scores.items():
        out[f"{key}_f"] = val.fmeasure
        out[f"{key}_r"] = val.recall
        out[f"{key}_p"] = val.precision
    return out


def _bleu_chrf(candidate: str, reference: str) -> dict[str, float]:
    import sacrebleu

    bleu = sacrebleu.sentence_bleu(candidate, [reference]).score
    chrf = sacrebleu.sentence_chrf(candidate, [reference]).score
    return {"bleu": bleu, "chrf": chrf}


# ------------------------------------------------------- critical-fact coverage
def _normalise(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[*_`#]", " ", text)          # markdown emphasis
    text = re.sub(r"\s+", " ", text)
    return text


def fact_coverage(output: str, must_include: Sequence[str]) -> dict[str, Any]:
    """Fraction of required facts present. ``a|b`` means either is acceptable."""
    if not must_include:
        return {"coverage": None, "hits": 0, "total": 0, "missing": []}
    hay = _normalise(output)
    missing: list[str] = []
    hits = 0
    for check in must_include:
        alternatives = [a.strip().lower() for a in check.split("|") if a.strip()]
        if any(alt in hay for alt in alternatives):
            hits += 1
        else:
            missing.append(check)
    return {
        "coverage": hits / len(must_include),
        "hits": hits,
        "total": len(must_include),
        "missing": missing,
    }


def _ensure_safe_import_path() -> None:
    """Remove the current working directory from import search paths.

    Python 3.13 can block imports from the current working directory for safety
    reasons when a local module shadows a dependency. The app and tests run from
    the repo root, so this keeps optional runtime dependencies like textstat/nltk
    importable without relying on the cwd being on sys.path.
    """
    cwd = str(Path.cwd())
    sys.path[:] = [p for p in sys.path if p not in {"", cwd}]


def _estimate_syllables(word: str) -> int:
    word = word.lower().strip("'\"")
    if not word:
        return 0
    vowels = "aeiouy"
    count = 0
    prev_vowel = False
    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if word.endswith(("e", "es", "ed")) and count > 1:
        count -= 1
    return max(1, count)


def _estimate_readability_metrics(body: str) -> dict[str, float]:
    words = re.findall(r"\b[\w']+\b", body)
    word_count = len(words)
    if word_count == 0:
        return {"fk_grade": 0.0, "smog": 0.0, "reading_ease": 0.0, "words": 0}

    sentence_count = max(1, len(re.findall(r"[.!?]+", body)))
    syllables = sum(_estimate_syllables(word) for word in words)
    words_per_sentence = word_count / sentence_count
    syllables_per_word = syllables / word_count
    complex_words = sum(1 for word in words if _estimate_syllables(word) >= 3)

    fk_grade = max(0.0, 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59)
    smog = max(0.0, 1.043 * math.sqrt(max(0.0, complex_words * 30 / sentence_count)) + 3.1291)
    reading_ease = max(0.0, 206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word)
    return {
        "fk_grade": round(float(fk_grade), 2),
        "smog": round(float(smog), 2),
        "reading_ease": round(float(reading_ease), 2),
        "words": word_count,
    }


# ------------------------------------------------------- readability / hygiene
def readability(text: str) -> dict[str, float]:
    _ensure_safe_import_path()
    body = _strip_headings(text)
    if len(body.split()) < 20:
        return {"fk_grade": 0.0, "smog": 0.0, "reading_ease": 0.0, "words": len(body.split())}

    try:
        import textstat
    except Exception:
        return _estimate_readability_metrics(body)

    return {
        "fk_grade": round(float(textstat.flesch_kincaid_grade(body)), 2),
        "smog": round(float(textstat.smog_index(body)), 2),
        "reading_ease": round(float(textstat.flesch_reading_ease(body)), 2),
        "words": len(body.split()),
    }


def _strip_headings(text: str) -> str:
    """Remove the all-caps section headings before scoring readability.

    Headings are not prose; leaving them in makes every system look worse and
    penalises the two structured systems relative to the unstructured one.
    """
    keep = [
        ln for ln in text.splitlines()
        if ln.strip() and ln.strip().rstrip(":") not in _HEADINGS
    ]
    return "\n".join(keep)


def abbreviation_leaks(text: str) -> dict[str, Any]:
    """Raw sig codes left in patient-facing text.

    Text inside brackets is exempt: "twice a day (BD)" is a legitimate gloss,
    whereas a bare "BD" is exactly the failure this project exists to fix.
    """
    exposed = re.sub(r"\([^)]*\)", " ", text)
    exposed = re.sub(r'"[^"]*"', " ", exposed)
    codes = sorted({m.group(1) for m in _LEAK_RE.finditer(exposed)})
    slots = [m.group(0) for m in _SLOT_LEAK_RE.finditer(exposed)]
    durations = [m.group(0) for m in _DURATION_LEAK_RE.finditer(exposed)]
    total = len(codes) + len(slots) + len(durations)
    return {
        "leak_count": total,
        "clean": total == 0,
        "codes": codes,
        "slot_patterns": slots,
        "duration_shorthand": durations,
    }


def structure_score(text: str) -> dict[str, Any]:
    upper = text.upper()
    present = [h for h in _HEADINGS if h in upper]
    return {
        "headings_present": len(present),
        "headings_total": len(_HEADINGS),
        "structure": len(present) / len(_HEADINGS),
        "missing_headings": [h for h in _HEADINGS if h not in upper],
    }


def has_disclaimer(text: str) -> bool:
    low = _normalise(text)
    cues = (
        "not medical advice",
        "does not replace your doctor",
        "consult your doctor",
        "follow your doctor",
    )
    return any(cue in low for cue in cues)


# ------------------------------------------------------------- LLM-as-judge
def judge_one(
    client: LLMClient,
    prescription: str,
    reference: str,
    candidate: str,
) -> dict[str, Any]:
    """Grade one explanation on the 1-5 rubric. Never raises."""
    try:
        resp = client.chat(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": JUDGE_USER.format(
                        prescription=prescription,
                        reference=reference,
                        candidate=candidate,
                    ),
                },
            ],
            max_tokens=1400,
            temperature=0.0,
            json_mode=True,
            deployment=client.cfg.judge_deployment,
        )
    except LLMError as exc:
        return {"error": str(exc)}

    from .systems import _extract_json

    data = _extract_json(resp.text)
    if not isinstance(data, dict):
        return {"error": "judge did not return JSON", "raw": resp.text[:300]}

    out: dict[str, Any] = {}
    for dim in JUDGE_DIMENSIONS:
        val = data.get(dim)
        try:
            score = int(round(float(val)))
        except (TypeError, ValueError):
            continue
        out[dim] = max(1, min(5, score))
    hall = data.get("hallucinations")
    out["hallucinations"] = [str(h) for h in hall] if isinstance(hall, list) else []
    out["hallucination_count"] = len(out["hallucinations"])
    out["verdict"] = str(data.get("one_line_verdict", ""))[:300]
    if len(out.get("hallucinations", [])) == 0 and not any(
        d in out for d in JUDGE_DIMENSIONS
    ):
        return {"error": "judge returned no scores", "raw": resp.text[:300]}
    scored = [out[d] for d in JUDGE_DIMENSIONS if d in out]
    out["mean"] = round(statistics.mean(scored), 3) if scored else None
    return out


# --------------------------------------------------------------- scoring a case
def score_explanation(
    exp: Explanation,
    case: GoldCase,
    *,
    client: LLMClient | None = None,
    use_judge: bool = False,
) -> dict[str, Any]:
    """All metrics for one (system, case) pair."""
    text = exp.output_text or ""
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "system": exp.system,
        "category": case.category,
        "difficulty": case.difficulty,
        "n_drugs": len(case.drugs),
        "error": exp.error,
        "latency_s": round(exp.latency_s, 3),
        "llm_calls": exp.llm_calls,
        "prompt_tokens": exp.prompt_tokens,
        "completion_tokens": exp.completion_tokens,
        "safety_flags": len(exp.safety_flags),
        "output_words": len(text.split()),
    }
    if not text:
        row["failed"] = True
        return row
    row["failed"] = False

    row.update(_rouge(text, case.reference))
    row.update(_bleu_chrf(text, case.reference))

    cov = fact_coverage(text, case.must_include)
    row["fact_coverage"] = cov["coverage"]
    row["facts_hit"] = cov["hits"]
    row["facts_total"] = cov["total"]
    row["facts_missing"] = "; ".join(cov["missing"])

    read = readability(text)
    row.update(read)
    row["in_target_band"] = TARGET_GRADE_LOW <= read["fk_grade"] <= TARGET_GRADE_HIGH

    leaks = abbreviation_leaks(text)
    row["leak_count"] = leaks["leak_count"]
    row["abbrev_clean"] = leaks["clean"]
    row["leaked"] = ", ".join(
        leaks["codes"] + leaks["slot_patterns"] + leaks["duration_shorthand"]
    )

    struct = structure_score(text)
    row["structure"] = struct["structure"]
    row["headings_present"] = struct["headings_present"]
    row["has_disclaimer"] = has_disclaimer(text)

    if use_judge and client is not None:
        verdict = judge_one(client, case.input_text, case.reference, text)
        if "error" in verdict:
            row["judge_error"] = verdict["error"]
        else:
            for dim in JUDGE_DIMENSIONS:
                if dim in verdict:
                    row[f"judge_{dim}"] = verdict[dim]
            row["judge_mean"] = verdict.get("mean")
            row["judge_hallucinations"] = verdict.get("hallucination_count", 0)
            row["judge_hallucination_list"] = " | ".join(verdict.get("hallucinations", []))
            row["judge_verdict"] = verdict.get("verdict", "")
    return row


# ------------------------------------------------------------------ aggregation
_MEAN_FIELDS = (
    "rouge1_f", "rouge1_r", "rouge2_f", "rouge2_r", "rougeL_f", "rougeL_r",
    "bleu", "chrf", "fact_coverage", "fk_grade", "smog", "reading_ease",
    "structure", "leak_count", "output_words", "latency_s", "safety_flags",
    "judge_factual_accuracy", "judge_completeness", "judge_simplicity",
    "judge_safety", "judge_mean", "judge_hallucinations",
)
_RATE_FIELDS = ("abbrev_clean", "has_disclaimer", "in_target_band", "failed")
_SUM_FIELDS = ("llm_calls", "prompt_tokens", "completion_tokens")


def _mean(values: Iterable[Any]) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(statistics.mean(nums), 4) if nums else None


def aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Per-system means, plus a per-category breakdown of the headline metrics."""
    out: dict[str, Any] = {}
    for system in SYSTEMS:
        subset = [r for r in rows if r["system"] == system]
        if not subset:
            continue
        ok = [r for r in subset if not r.get("failed")]
        agg: dict[str, Any] = {"n": len(subset), "n_scored": len(ok)}
        for field in _MEAN_FIELDS:
            val = _mean(r.get(field) for r in ok)
            if val is not None:
                agg[field] = val
        for field in _RATE_FIELDS:
            vals = [bool(r.get(field)) for r in subset if field in r]
            if vals:
                agg[f"{field}_rate"] = round(sum(vals) / len(vals), 4)
        for field in _SUM_FIELDS:
            agg[field] = sum(int(r.get(field) or 0) for r in subset)
        agg["judge_errors"] = sum(1 for r in subset if r.get("judge_error"))

        by_cat: dict[str, Any] = {}
        for cat in sorted({r["category"] for r in subset}):
            cat_rows = [r for r in ok if r["category"] == cat]
            if not cat_rows:
                continue
            by_cat[cat] = {
                "n": len(cat_rows),
                "fact_coverage": _mean(r.get("fact_coverage") for r in cat_rows),
                "rougeL_r": _mean(r.get("rougeL_r") for r in cat_rows),
                "judge_mean": _mean(r.get("judge_mean") for r in cat_rows),
                "leak_count": _mean(r.get("leak_count") for r in cat_rows),
            }
        agg["by_category"] = by_cat

        by_diff: dict[str, Any] = {}
        for diff in ("easy", "medium", "hard"):
            d_rows = [r for r in ok if r["difficulty"] == diff]
            if not d_rows:
                continue
            by_diff[diff] = {
                "n": len(d_rows),
                "fact_coverage": _mean(r.get("fact_coverage") for r in d_rows),
                "judge_mean": _mean(r.get("judge_mean") for r in d_rows),
            }
        agg["by_difficulty"] = by_diff
        out[system] = agg
    return out


# ------------------------------------------------------------------- reporting
_HEADLINE = (
    ("fact_coverage", "Fact coverage", "{:.3f}"),
    ("judge_mean", "Judge mean (1-5)", "{:.2f}"),
    ("judge_factual_accuracy", "Judge accuracy", "{:.2f}"),
    ("judge_completeness", "Judge completeness", "{:.2f}"),
    ("judge_simplicity", "Judge simplicity", "{:.2f}"),
    ("judge_safety", "Judge safety", "{:.2f}"),
    ("judge_hallucinations", "Hallucinations / case", "{:.2f}"),
    ("rouge1_r", "ROUGE-1 recall", "{:.3f}"),
    ("rouge2_r", "ROUGE-2 recall", "{:.3f}"),
    ("rougeL_r", "ROUGE-L recall", "{:.3f}"),
    ("rouge1_f", "ROUGE-1 F1", "{:.3f}"),
    ("rougeL_f", "ROUGE-L F1", "{:.3f}"),
    ("bleu", "BLEU", "{:.2f}"),
    ("chrf", "chrF", "{:.2f}"),
    ("fk_grade", "Flesch-Kincaid grade", "{:.2f}"),
    ("smog", "SMOG index", "{:.2f}"),
    ("reading_ease", "Reading ease", "{:.1f}"),
    ("in_target_band_rate", "In grade 6-8 band", "{:.2f}"),
    ("abbrev_clean_rate", "No abbreviation leaks", "{:.2f}"),
    ("leak_count", "Leaks / case", "{:.2f}"),
    ("has_disclaimer_rate", "Disclaimer present", "{:.2f}"),
    ("structure", "Structure compliance", "{:.2f}"),
    ("safety_flags", "Safety flags / case", "{:.2f}"),
    ("output_words", "Output words", "{:.0f}"),
    ("latency_s", "Latency (s)", "{:.2f}"),
    ("llm_calls", "Total LLM calls", "{:.0f}"),
    ("completion_tokens", "Completion tokens", "{:.0f}"),
)

_LABELS = {"rule": "A: Rule-based", "zeroshot": "B: Zero-shot LLM", "sota": "C: SOTA pipeline"}


def _fmt(value: Any, spec: str) -> str:
    if value is None:
        return "-"
    try:
        return spec.format(value)
    except (TypeError, ValueError):
        return str(value)


def markdown_report(agg: dict[str, Any], meta: dict[str, Any]) -> str:
    systems = [s for s in SYSTEMS if s in agg]
    lines = ["# Evaluation results", ""]
    lines.append(f"- Gold cases: **{meta.get('n_cases')}**")
    lines.append(f"- Systems: {', '.join(_LABELS.get(s, s) for s in systems)}")
    lines.append(f"- LLM judge: **{'on' if meta.get('judge') else 'off'}**")
    if meta.get("model"):
        lines.append(f"- Generation deployment: `{meta['model']}`")
    if meta.get("judge_model"):
        lines.append(f"- Judge deployment: `{meta['judge_model']}`")
    lines.append(f"- Run finished: {meta.get('finished_at')}")
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | " + " | ".join(_LABELS.get(s, s) for s in systems) + " |")
    lines.append("|---|" + "---|" * len(systems))
    for key, label, spec in _HEADLINE:
        vals = [agg[s].get(key) for s in systems]
        if all(v is None for v in vals):
            continue
        lines.append(f"| {label} | " + " | ".join(_fmt(v, spec) for v in vals) + " |")
    lines.append("")

    lines.append("## Fact coverage by category")
    lines.append("")
    cats = sorted({c for s in systems for c in agg[s].get("by_category", {})})
    lines.append("| Category | n | " + " | ".join(_LABELS.get(s, s) for s in systems) + " |")
    lines.append("|---|---|" + "---|" * len(systems))
    for cat in cats:
        n = next(
            (agg[s]["by_category"][cat]["n"] for s in systems if cat in agg[s].get("by_category", {})),
            0,
        )
        cells = [
            _fmt((agg[s].get("by_category", {}).get(cat) or {}).get("fact_coverage"), "{:.3f}")
            for s in systems
        ]
        lines.append(f"| {cat} | {n} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Fact coverage by difficulty")
    lines.append("")
    lines.append("| Difficulty | n | " + " | ".join(_LABELS.get(s, s) for s in systems) + " |")
    lines.append("|---|---|" + "---|" * len(systems))
    for diff in ("easy", "medium", "hard"):
        entries = [agg[s].get("by_difficulty", {}).get(diff) for s in systems]
        if all(e is None for e in entries):
            continue
        n = next((e["n"] for e in entries if e), 0)
        cells = [_fmt((e or {}).get("fact_coverage"), "{:.3f}") for e in entries]
        lines.append(f"| {diff} | {n} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "> ROUGE/BLEU are reported recall-first. The gold references are compact "
        "paragraphs while systems A and C emit a longer six-section counselling "
        "document, so overlap precision (and therefore F1) is structurally "
        "depressed and should not be read as a quality ranking on its own."
    )
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _load_checkpoint(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Replay a checkpoint file into a {(system, case_id): record} map.

    A hard kill can leave a torn final line, so unparseable lines are dropped
    rather than fatal that pair simply gets re-run.
    """
    if not path.exists():
        return {}
    done: dict[tuple[str, str], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            done[(rec["row"]["system"], rec["row"]["case_id"])] = rec
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return done


# ------------------------------------------------------------------ the run loop
def run_evaluation(
    systems: Sequence[str] = SYSTEMS,
    *,
    cases: Sequence[GoldCase] | None = None,
    client: LLMClient | None = None,
    use_judge: bool = True,
    offline: bool = False,
    verify: bool = True,
    out_dir: Path | None = None,
    progress: bool = True,
    resume: bool = False,
) -> dict[str, Any]:
    """Run the requested systems over the gold set and write all artefacts.

    Every scored (system, case) pair is appended to ``_checkpoint.jsonl`` as it
    completes. The full run is ~2h of paid LLM calls, so losing it to a stray
    interrupt is expensive; ``resume=True`` replays that file and re-runs only
    what is missing. The checkpoint is removed once the real artefacts land.
    """
    cases = list(cases) if cases is not None else load_gold()
    out_dir = out_dir or RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    needs_llm = any(s != "rule" for s in systems)
    if (needs_llm or use_judge) and client is None:
        raise ValueError("an LLM client is required for the zero-shot/SOTA systems and the judge")
    if use_judge and client is None:
        use_judge = False

    rows: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    started = time.time()

    ckpt_path = out_dir / "_checkpoint.jsonl"
    done = _load_checkpoint(ckpt_path) if resume else {}
    if done and progress:
        print(f"  resuming: {len(done)} pair(s) already scored in {ckpt_path.name}",
              flush=True)
    if not resume and ckpt_path.exists():
        ckpt_path.unlink()

    ckpt = ckpt_path.open("a", encoding="utf-8")
    try:
        for system in systems:
            for i, case in enumerate(cases, 1):
                cached = done.get((system, case.case_id))
                if cached is not None:
                    if progress:
                        print(f"  [{system}] {i}/{len(cases)} {case.case_id} (cached)",
                              flush=True)
                    rows.append(cached["row"])
                    outputs.append(cached["output"])
                    continue
                if progress:
                    print(f"  [{system}] {i}/{len(cases)} {case.case_id}", flush=True)
                try:
                    exp = run_system(
                        system, case.case_id, case.input_text, client,
                        offline=offline, verify=verify,
                    )
                except Exception as exc:                   # keep the run alive
                    exp = Explanation(
                        case_id=case.case_id, system=system,
                        input_text=case.input_text, output_text="",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                row = score_explanation(exp, case, client=client, use_judge=use_judge)
                output = {
                    "case_id": case.case_id,
                    "system": system,
                    "category": case.category,
                    "input_text": case.input_text,
                    "reference": case.reference,
                    "output_text": exp.output_text,
                    "safety_flags": [asdict(f) for f in exp.safety_flags],
                    "error": exp.error,
                    "meta": exp.meta,
                }
                rows.append(row)
                outputs.append(output)
                # Flush per pair: an interrupt must cost one case, not the run.
                ckpt.write(json.dumps({"row": row, "output": output},
                                      ensure_ascii=False) + "\n")
                ckpt.flush()
    finally:
        ckpt.close()

    agg = aggregate(rows)
    meta = {
        "n_cases": len(cases),
        "systems": list(systems),
        "judge": use_judge,
        "offline_retrieval": offline,
        "self_verification": verify,
        "model": client.cfg.deployment if client else None,
        "judge_model": client.cfg.judge_deployment if client else None,
        "elapsed_s": round(time.time() - started, 1),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        # When resumed, elapsed_s and llm_usage cover only this invocation --
        # the cached pairs cost nothing here but were paid for earlier.
        "resumed_pairs": len(done),
        "gold_stats": gold_stats(cases),
        "llm_usage": client.usage.to_dict() if client else None,
    }

    (out_dir / "metrics.json").write_text(
        json.dumps({"meta": meta, "aggregate": agg, "per_case": rows}, indent=2),
        encoding="utf-8",
    )
    write_csv(out_dir / "per_case.csv", rows)
    (out_dir / "outputs.jsonl").write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in outputs) + "\n",
        encoding="utf-8",
    )
    (out_dir / "RESULTS.md").write_text(markdown_report(agg, meta), encoding="utf-8")
    # The real artefacts are on disk; the checkpoint has done its job.
    ckpt_path.unlink(missing_ok=True)
    return {"meta": meta, "aggregate": agg, "per_case": rows, "out_dir": str(out_dir)}


def summary_table(agg: dict[str, Any]) -> str:
    """Short console table for the CLI."""
    systems = [s for s in SYSTEMS if s in agg]
    keys = (
        ("fact_coverage", "facts", "{:.3f}"),
        ("judge_mean", "judge", "{:.2f}"),
        ("rougeL_r", "rougeL_r", "{:.3f}"),
        ("bleu", "bleu", "{:.1f}"),
        ("fk_grade", "grade", "{:.1f}"),
        ("abbrev_clean_rate", "clean", "{:.2f}"),
        ("latency_s", "sec", "{:.2f}"),
    )
    head = f"{'system':<10}" + "".join(f"{label:>10}" for _, label, _ in keys)
    lines = [head, "-" * len(head)]
    for system in systems:
        cells = "".join(_fmt(agg[system].get(k), spec).rjust(10) for k, _, spec in keys)
        lines.append(f"{system:<10}{cells}")
    return "\n".join(lines)
