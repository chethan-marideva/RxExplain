

from __future__ import annotations

import json
import re
import time
from typing import Any

from . import drugkb, parser_rules, prompts, retrieval, safety
from .llm import LLMClient
from .schema import Explanation, Medication, ParsedPrescription

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_SLOT_BUCKETS = ("Morning", "Afternoon", "Evening", "Night")


def _how_to_take(med: Medication) -> str:
    """One plain sentence describing when and how to take this medicine."""
    bits: list[str] = []
    if med.frequency_human:
        if med.dose_pattern:
            bits.append(med.frequency_human)          # already "1 tablet in the morning ..."
        else:
            amount = med.dose_amount or f"1 {med.form or 'dose'}"
            bits.append(f"{amount} {med.frequency_human}")
    elif med.dose_amount:
        bits.append(med.dose_amount)
    else:
        bits.append("the amount is not written on this prescription")

    if med.timing:
        bits.append(med.timing)
    if med.route and med.route != "by mouth":
        bits.append(med.route)
    if med.prn:
        bits.append("and only when you need it, not every day")
    sentence = ", ".join(bits)
    return sentence[0].upper() + sentence[1:] if sentence else ""


def _day_plan(meds: list[Medication]) -> list[str]:
    """Group the medicines into a morning / afternoon / evening / night list."""
    buckets: dict[str, list[str]] = {b: [] for b in _SLOT_BUCKETS}
    other: list[str] = []

    for med in meds:
        label = med.name or med.generic or "this medicine"
        
        amount = med.dose_amount or f"1 {med.form or 'dose'}"
        timing = f" ({med.timing})" if med.timing else ""

        freq = (med.frequency_human or "").lower()
        slots: list[str] = []
        if med.dose_pattern:
            # the parser already spelled out which slots are used
            for bucket in _SLOT_BUCKETS:
                if bucket.lower() in freq:
                    slots.append(bucket)
            if "at night" in freq and "Night" not in slots:
                slots.append("Night")
        elif "week" in freq or "month" in freq or "other day" in freq:
            other.append(f"{label}: {med.frequency_human}{timing}")
            continue
        elif "bedtime" in freq or "at night" in freq:
            slots = ["Night"]
        elif "in the morning" in freq or (med.timing or "").startswith("early"):
            slots = ["Morning"]
        elif "four times" in freq:
            slots = ["Morning", "Afternoon", "Evening", "Night"]
        elif "three times" in freq:
            slots = ["Morning", "Afternoon", "Night"]
        elif "twice" in freq:
            slots = ["Morning", "Night"]
        elif "once a day" in freq:
            slots = ["Morning"]

        if not slots:
            other.append(f"{label}: {_how_to_take(med).lower()}")
            continue
        for bucket in slots:
            buckets[bucket].append(f"{amount} {label}{timing}")

    lines: list[str] = []
    for bucket in _SLOT_BUCKETS:
        if buckets[bucket]:
            lines.append(f"- {bucket}: " + "; ".join(buckets[bucket]))
    for entry in other:
        lines.append(f"- {entry}")
    return lines


def _kb_key(med: Medication) -> str | None:
    for note in med.notes:
        if note.startswith("kb:"):
            return note.split(":")[1]
    return None


# ---------------------------------------------------------------------------
# A. rule based baseline
# ---------------------------------------------------------------------------

def explain_rule(case_id: str, text: str) -> Explanation:
    """Deterministic template expansion. No LLM and no network access."""
    started = time.perf_counter()
    parsed = parser_rules.parse(text)
    flags = safety.check(parsed)

    n = len(parsed.medications)
    out: list[str] = ["WHAT THIS PRESCRIPTION IS FOR"]
    if n == 0:
        out.append(
            "This prescription could not be read automatically. Please ask your "
            "pharmacist to read it with you."
        )
    else:
        purposes = [m.purpose for m in parsed.medications if m.purpose]
        word = "medicine" if n == 1 else "medicines"
        head = f"You have been given {n} {word}."
        if purposes:
            head += " Together they are meant " + _join(purposes[:3]) + "."
        out.append(head)

    out += ["", "YOUR MEDICINES"]
    for i, med in enumerate(parsed.medications, 1):
        key = _kb_key(med)
        entry = drugkb.get(key) if key else None
        title = med.name or "Unnamed medicine"
        if entry:
            strength = f" {med.strength}" if med.strength else ""
            title = f"{med.name} ({entry['generic'].lower()}{strength} {med.form or 'dose'})"
        out.append(f"{i}. {title}")
        if entry:
            out.append(
                f"   What it is for: {entry['purpose']}. "
                f"It is a {entry['class'].lower()}."
            )
            out.append(f"   How it works: {entry['how_it_works']}")
        elif med.purpose:
            out.append(f"   What it is for: {med.purpose}.")
        else:
            out.append(
                "   What it is for: this medicine is not in our reference list. "
                "Please ask your pharmacist what it treats."
            )
        out.append(f"   How to take it: {_how_to_take(med)}.")
        if med.duration:
            out.append(f"   How long: {med.duration}.")
        else:
            out.append(
                "   How long: not stated on this prescription. Ask your doctor "
                "how long to continue."
            )
        if entry:
            side = ", ".join(entry["common_side_effects"][:3])
            out.append(f"   What you may notice: {side}.")
            if entry["key_advice"]:
                out.append(f"   Remember: {entry['key_advice'][0]}")
        for note in med.notes:
            if note.startswith("indication: "):
                out.append(f"   Prescribed for: {note.split(': ', 1)[1]}.")
            elif note.startswith("on "):
                out.append(f"   Take it {note}.")
        out.append("")

    plan = _day_plan(parsed.medications)
    if plan:
        out += ["HOW TO TAKE THEM THROUGH THE DAY", *plan, ""]

    out.append("IMPORTANT SAFETY POINTS")
    if flags:
        for f in flags:
            out.append(f"- {f.message}")
    advice_seen: set[str] = set()
    for med in parsed.medications:
        key = _kb_key(med)
        entry = drugkb.get(key) if key else None
        if not entry:
            continue
        for advice in entry["key_advice"][1:]:
            if advice not in advice_seen:
                advice_seen.add(advice)
                out.append(f"- {med.name}: {advice}")
    if not flags and not advice_seen:
        out.append(
            "- Take each medicine exactly as written above, and finish any "
            "antibiotic course completely."
        )
    for instruction in parsed.general_instructions:
        out.append(f"- Your doctor also advised: {instruction}")
    out.append("")

    out.append("WHEN TO CALL YOUR DOCTOR")
    serious_seen: set[str] = set()
    for med in parsed.medications:
        key = _kb_key(med)
        entry = drugkb.get(key) if key else None
        if not entry:
            continue
        for item in entry["serious_side_effects"][:3]:
            if item not in serious_seen:
                serious_seen.add(item)
                out.append(f"- {med.name}: {item}.")
    out.append(
        "- Any sudden rash, swelling of the face or lips, or difficulty "
        "breathing. Treat this as an emergency."
    )
    out += ["", "DISCLAIMER", prompts.DISCLAIMER]

    return Explanation(
        case_id=case_id,
        system="rule",
        input_text=text,
        output_text="\n".join(out).strip(),
        medications=parsed.medications,
        safety_flags=flags,
        latency_s=time.perf_counter() - started,
        llm_calls=0,
        meta={
            "parser_coverage": round(parsed.coverage, 3),
            "unparsed_lines": len(parsed.unparsed_lines),
            "kb_hits": sum(1 for m in parsed.medications if _kb_key(m)),
        },
    )


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------
# B. zero shot LLM baseline
# ---------------------------------------------------------------------------

def explain_zeroshot(case_id: str, text: str, client: LLMClient) -> Explanation:
    """One naive prompt. No parsing, no knowledge base, no safety layer."""
    started = time.perf_counter()
    messages = [
        {"role": "system", "content": prompts.ZEROSHOT_SYSTEM},
        {"role": "user", "content": prompts.ZEROSHOT_USER.format(prescription=text)},
    ]
    try:
        resp = client.chat(messages, max_tokens=1600, temperature=0.2)
        return Explanation(
            case_id=case_id,
            system="zeroshot",
            input_text=text,
            output_text=resp.text,
            latency_s=time.perf_counter() - started,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            llm_calls=1,
            meta={"finish_reason": resp.finish_reason, "model": resp.model},
        )
    except Exception as exc:
        return Explanation(
            case_id=case_id, system="zeroshot", input_text=text, output_text="",
            latency_s=time.perf_counter() - started, llm_calls=1,
            error=f"{type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# C. SOTA pipeline
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object out of a model reply, tolerating code fences."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except json.JSONDecodeError:
        pass
    start, depth = None, 0
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    val = json.loads(text[start:i + 1])
                    return val if isinstance(val, dict) else None
                except json.JSONDecodeError:
                    start = None
    return None


def _line_from_extraction(item: dict[str, Any]) -> str:
    """Rebuild a canonical prescription line from LLM-extracted fields.

    Re-running the deterministic parser over this line means the LLM never gets
    to invent the dose arithmetic - it only supplies the strings.
    """
    form = (item.get("form") or "").strip()
    form_prefix = {
        "tablet": "Tab.", "capsule": "Cap.", "syrup": "Syp.",
        "injection": "Inj.", "drops": "Drop.", "inhaler": "Inh.",
        "cream": "Cream", "ointment": "Oint.",
    }.get(form.lower(), "")
    parts = [
        form_prefix,
        str(item.get("written_name") or "").strip(),
        str(item.get("strength") or "").strip(),
        str(item.get("dose_amount") or "").strip(),
        str(item.get("schedule") or "").strip(),
        str(item.get("timing") or "").strip(),
        str(item.get("route") or "").strip(),
    ]
    line = " ".join(p for p in parts if p)
    duration = str(item.get("duration") or "").strip()
    if duration:
        line += f" x {duration}"
    if item.get("as_needed"):
        line += " SOS"
    indication = str(item.get("indication") or "").strip()
    if indication and indication.lower() not in ("null", "none"):
        line += f" for {indication}"
    return line.strip()


def _merge_extraction(
    parsed: ParsedPrescription, data: dict[str, Any]
) -> tuple[int, int]:
    """Add medicines the rule parser missed. Returns (added, llm_reported)."""
    items = data.get("medications")
    if not isinstance(items, list):
        return 0, 0

    def norm(s: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    known = {norm(m.name) for m in parsed.medications}
    known |= {norm(m.generic) for m in parsed.medications}
    added = 0

    for item in items:
        if not isinstance(item, dict):
            continue
        written = str(item.get("written_name") or "").strip()
        if not written:
            continue
        nw = norm(written)
        if not nw or nw in known:
            continue
        # does it match a medicine we already have, under a different spelling?
        if any(nw in k or k in nw for k in known if len(k) > 3):
            continue

        line = _line_from_extraction(item)
        med = parser_rules.parse_line(line) if line else None
        if med is None:
            med = Medication(raw=line or written, name=written)
            hit = drugkb.resolve(written)
            if hit:
                med.generic = hit[1]["generic"]
                med.purpose = hit[1]["purpose"]
                med.notes.append(f"kb:{hit[0]}:{hit[2]}:{hit[3]}")
        med.notes.append("recovered by LLM extraction")
        parsed.medications.append(med)
        known.add(nw)
        added += 1

    # any unparsed line whose text is now covered by a recovered medicine
    if added:
        remaining: list[str] = []
        names = [norm(m.name) for m in parsed.medications if m.name]
        for line in parsed.unparsed_lines:
            nl = norm(line)
            if any(n and n in nl for n in names):
                continue
            remaining.append(line)
        parsed.unparsed_lines = remaining

    for instr in data.get("general_instructions") or []:
        if isinstance(instr, str) and instr.strip() and \
                instr.strip() not in parsed.general_instructions:
            parsed.general_instructions.append(instr.strip())

    return added, len(items)


def _reference_block(
    parsed: ParsedPrescription, *, offline: bool
) -> tuple[str, dict[str, Any]]:
    """Assemble KB + retrieved label grounding for every medicine."""
    blocks: list[str] = []
    meta: dict[str, Any] = {"rxcui": {}, "labels": {}, "kb": {}}

    for med in parsed.medications:
        key = _kb_key(med)
        entry = drugkb.get(key) if key else None
        name = med.name or key or "unknown"
        lines = [f"### {name}"]

        if entry:
            meta["kb"][name] = key
            lines += [
                f"generic: {entry['generic']}",
                f"class: {entry['class']}",
                f"purpose: {entry['purpose']}",
                f"how it works: {entry['how_it_works']}",
                f"common side effects: {', '.join(entry['common_side_effects'])}",
                f"serious side effects: {', '.join(entry['serious_side_effects'])}",
                f"key advice: {' | '.join(entry['key_advice'])}",
                f"caution if: {', '.join(entry['avoid_or_caution_if'])}",
                f"usual adult maximum per day: {entry['max_daily_dose']} "
                f"{entry['dose_unit']}",
            ]
        else:
            lines.append(
                "NOT IN THE KNOWLEDGE BASE. Do not state what this medicine does "
                "or its side effects. Tell the patient to ask their pharmacist."
            )

        grounding = retrieval.ground_medication(med.name, entry, offline=offline)
        if grounding.get("rxcui"):
            meta["rxcui"][name] = grounding["rxcui"]
            lines.append(f"RxNorm concept id: {grounding['rxcui']}")
        ctx = retrieval.label_context(grounding)
        if ctx:
            meta["labels"][name] = (grounding["label"] or {}).get("generic_name")
            lines.append(ctx)

        blocks.append("\n".join(lines))

    return ("\n\n".join(blocks) or "(no reference information available)"), meta


def explain_sota(
    case_id: str,
    text: str,
    client: LLMClient,
    *,
    offline: bool = False,
    verify: bool = True,
) -> Explanation:
    """The full engineered pipeline."""
    started = time.perf_counter()
    calls = 0
    ptok = ctok = 0
    stages: dict[str, Any] = {}

    # -- stage 1: deterministic parse -------------------------------------
    parsed = parser_rules.parse(text)
    stages["rule_parsed"] = len(parsed.medications)
    stages["rule_coverage"] = round(parsed.coverage, 3)

    # -- stage 2: LLM extraction, to recover what the rules missed --------
    try:
        resp = client.chat(
            [
                {"role": "system", "content": prompts.EXTRACTION_SYSTEM},
                {"role": "user", "content": prompts.EXTRACTION_USER.format(
                    prescription=text)},
            ],
            max_tokens=1600, temperature=0.0, json_mode=True,
        )
        calls += 1
        ptok += resp.prompt_tokens
        ctok += resp.completion_tokens
        data = _extract_json(resp.text) or {}
        added, reported = _merge_extraction(parsed, data)
        stages["llm_extracted"] = reported
        stages["recovered_by_llm"] = added
    except Exception as exc:
        stages["extraction_error"] = f"{type(exc).__name__}: {exc}"

    # -- stage 3: safety layer (deterministic, on the merged parse) -------
    flags = safety.check(parsed)
    stages["safety_flags"] = len(flags)
    stages["worst_severity"] = safety.worst_severity(flags)

    # -- stage 4: terminology + label retrieval ---------------------------
    reference, retrieved = _reference_block(parsed, offline=offline)
    stages["labels_retrieved"] = len(retrieved.get("labels", {}))
    stages["rxcuis_resolved"] = len(retrieved.get("rxcui", {}))

    facts = parser_rules.parsed_summary(parsed)
    safety_text = "\n".join(f"- [{f.severity}] {f.message}" for f in flags) or \
        "(no automatic safety flags were raised)"

    # -- stage 5: grounded few shot generation ----------------------------
    system = prompts.SOTA_SYSTEM.format(
        reading_level=prompts.READING_LEVEL, structure=prompts.TARGET_STRUCTURE
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for ex_in, ex_out in prompts.FEWSHOT:
        messages.append({"role": "user", "content": f"Prescription:\n{ex_in}"})
        messages.append({"role": "assistant", "content": ex_out})
    messages.append({"role": "user", "content": prompts.SOTA_USER.format(
        facts=facts, reference=reference, safety=safety_text,
        disclaimer=prompts.DISCLAIMER, prescription=text,
    )})

    try:
        resp = client.chat(messages, max_tokens=2600, temperature=0.2)
        calls += 1
        ptok += resp.prompt_tokens
        ctok += resp.completion_tokens
        draft = resp.text
    except Exception as exc:
        return Explanation(
            case_id=case_id, system="sota", input_text=text, output_text="",
            medications=parsed.medications, safety_flags=flags,
            latency_s=time.perf_counter() - started, prompt_tokens=ptok,
            completion_tokens=ctok, llm_calls=calls, retrieved=retrieved,
            error=f"generation failed: {type(exc).__name__}: {exc}", meta=stages,
        )

    # -- stage 6: self verification and targeted repair -------------------
    if verify and draft:
        try:
            vresp = client.chat(
                [
                    {"role": "system", "content": prompts.VERIFY_SYSTEM},
                    {"role": "user", "content": prompts.VERIFY_USER.format(
                        facts=facts, draft=draft)},
                ],
                max_tokens=1400, temperature=0.0, json_mode=True,
            )
            calls += 1
            ptok += vresp.prompt_tokens
            ctok += vresp.completion_tokens
            verdict = _extract_json(vresp.text) or {}
            issues = verdict.get("issues") or []
            issues = [i for i in issues if isinstance(i, dict)]
            stages["verify_issues"] = len(issues)
            stages["verify_kinds"] = sorted({str(i.get("kind")) for i in issues})

            if issues and not verdict.get("ok"):
                issue_text = "\n".join(
                    f"- [{i.get('kind')}] {i.get('detail')} -> {i.get('fix')}"
                    for i in issues
                )
                rresp = client.chat(
                    [
                        {"role": "system", "content": prompts.REPAIR_SYSTEM},
                        {"role": "user", "content": prompts.REPAIR_USER.format(
                            facts=facts, issues=issue_text, draft=draft)},
                    ],
                    max_tokens=2600, temperature=0.0,
                )
                calls += 1
                ptok += rresp.prompt_tokens
                ctok += rresp.completion_tokens
                if rresp.text:
                    draft = rresp.text
                    stages["repaired"] = True
        except Exception as exc:
            stages["verify_error"] = f"{type(exc).__name__}: {exc}"

    return Explanation(
        case_id=case_id,
        system="sota",
        input_text=text,
        output_text=draft,
        medications=parsed.medications,
        safety_flags=flags,
        latency_s=time.perf_counter() - started,
        prompt_tokens=ptok,
        completion_tokens=ctok,
        llm_calls=calls,
        retrieved=retrieved,
        meta=stages,
    )


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def run_system(
    system: str,
    case_id: str,
    text: str,
    client: LLMClient | None = None,
    *,
    offline: bool = False,
    verify: bool = True,
) -> Explanation:
    if system == "rule":
        return explain_rule(case_id, text)
    if client is None:
        raise ValueError(f"system {system!r} needs an LLM client")
    if system == "zeroshot":
        return explain_zeroshot(case_id, text, client)
    if system == "sota":
        return explain_sota(case_id, text, client, offline=offline, verify=verify)
    raise ValueError(f"unknown system: {system!r}")
