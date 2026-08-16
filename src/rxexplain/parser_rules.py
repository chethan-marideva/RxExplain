

from __future__ import annotations

import re

from . import drugkb
from .abbrev import (
    DAY_FULL,
    DAY_OF_WEEK_RE,
    DOSAGE_FORMS,
    DURATION_EXPLICIT,
    DURATION_SHORTHAND,
    DURATION_WORDS,
    FREQUENCIES,
    FREQUENCY_PHRASES,
    GENERAL_INSTRUCTION_CUES,
    INDICATION_RE,
    PRN_RE,
    ROUTES,
    SLOT_PATTERN,
    STAT_RE,
    STRENGTH_PATTERN,
    SYMPTOM_RE,
    TIMING_PHRASES,
    TIMINGS,
    UNIT_DISPLAY,
    describe_slots,
    fmt_qty,
    norm_key,
    slot_value,
    total_daily_units,
)
from .schema import Medication, ParsedPrescription

# Lines that only label a section of the prescription sheet.
SECTION_HEADERS = re.compile(
    r"^\s*(rx|r/x|advice|advise|diagnosis|dx|investigation[s]?|complaint[s]?|"
    r"c/o|history|h/o|examination|o/e|treatment|medication[s]?|"
    r"general\s+advice|follow\s*up|f/u|review|plan|note[s]?)\s*[:\-]?\s*$",
    re.IGNORECASE,
)

LEADING_JUNK = re.compile(r"^\s*(?:\d+\s*[.)\]]|[-*•‣●>]+)\s*")

DOSE_AMOUNT = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?(?:/\d+)?|½|¼|¾)\s*"
    r"(tabs?|tablets?|caps?|capsules?|tsp|teaspoons?|tbsp|tablespoons?|ml|cc|"
    r"drops?|puffs?|units?|sachets?|scoops?|sprays?|applications?|"
    r"suppositor(?:y|ies)|pessar(?:y|ies))\b",
    re.IGNORECASE,
)

FORM_PREFIX = re.compile(
    r"^\s*(" + "|".join(sorted((re.escape(k) for k in DOSAGE_FORMS), key=len, reverse=True))
    + r")\s*\.?\s+",
    re.IGNORECASE,
)

FORM_SUFFIX = re.compile(
    r"\b(tablets?|capsules?|syrups?|suspensions?|injections?|ointments?|creams?|"
    r"gels?|lotions?|drops?|inhalers?|sachets?|powders?|patch(?:es)?|sprays?|"
    r"nebulisations?|nebulizations?)\b",
    re.IGNORECASE,
)

# Filler words that are never part of a drug name.
NAME_NOISE = re.compile(
    r"\b(x|×|for|then|and|of|take|takes|taking|give|given|apply|instill|inhale|"
    r"use|using|start|continue|nos?|qty|each|every|daily|day|days|week|weeks|"
    r"month|months|year|years|hourly|hrs?|hours?|once|twice|thrice|times|weekly|"
    r"alternate|other|empty|stomach|food|meals?|milk|water|before|after|with|at|"
    r"in|the|on|to|as|when|needed|required|sos|prn|stat|immediately|tapering|"
    r"tapper|dose|doses)\b",
    re.IGNORECASE,
)

_AMOUNT_NOUN = {
    "tab": "tablet", "tabs": "tablet", "tablet": "tablet", "tablets": "tablet",
    "cap": "capsule", "caps": "capsule", "capsule": "capsule", "capsules": "capsule",
    "tsp": "teaspoon (5 ml)", "teaspoon": "teaspoon (5 ml)", "teaspoons": "teaspoon (5 ml)",
    "tbsp": "tablespoon (15 ml)", "tablespoon": "tablespoon (15 ml)",
    "tablespoons": "tablespoon (15 ml)",
    "ml": "ml", "cc": "ml",
    "drop": "drop", "drops": "drop",
    "puff": "puff", "puffs": "puff",
    "unit": "unit", "units": "unit",
    "sachet": "sachet", "sachets": "sachet",
    "scoop": "scoop", "scoops": "scoop",
    "spray": "spray", "sprays": "spray",
    "application": "application", "applications": "application",
    "suppository": "suppository", "suppositories": "suppository",
    "pessary": "pessary", "pessaries": "pessary",
}

# "50mg/5ml" - the denominator is the concentration basis, not a dose to take.
PER_VOLUME = re.compile(r"/\s*\d+(?:\.\d+)?\s*(?:ml|l|g|kg|dose|tab|tablet)\b", re.IGNORECASE)
# "for child 12kg", "adult 60 kg" - patient descriptors, not part of the name.
PATIENT_NOISE = re.compile(
    r"\b(?:\d+(?:\.\d+)?\s*kgs?|child(?:ren)?|adults?|baby|infant|patient|"
    r"years?\s*old|yrs?\s*old|male|female)\b",
    re.IGNORECASE,
)

_BLANK = " "
# A bare trailing number this small means "how many to take", not a strength.
_MAX_BARE_DOSE_COUNT = 4


def _cut(text: str, start: int, end: int) -> str:
    """Blank out a matched span so later patterns cannot re-match it."""
    return text[:start] + _BLANK * (end - start) + text[end:]


def _looks_like_instruction(line: str) -> bool:
    low = line.lower()
    if any(cue in low for cue in GENERAL_INSTRUCTION_CUES):
        has_form = bool(FORM_PREFIX.match(line))
        has_strength = bool(STRENGTH_PATTERN.search(line))
        has_slots = bool(SLOT_PATTERN.search(line))
        if not (has_form or has_strength or has_slots):
            return True
    return False


def _parse_duration(text: str) -> tuple[str | None, str]:
    m = DURATION_SHORTHAND.search(text)
    if m:
        n, denom = int(m.group(1)), m.group(2)
        word = {"7": "day", "52": "week", "12": "month"}[denom]
        label = f"{n} {word}" + ("s" if n != 1 else "")
        return label, _cut(text, *m.span())

    for m in DURATION_EXPLICIT.finditer(text):
        n = int(m.group(1))
        unit = DURATION_WORDS.get(m.group(2).lower())
        if not unit:
            continue
        label = f"{n} {unit}" + ("s" if n != 1 else "")
        return label, _cut(text, *m.span())
    return None, text


def _parse_strength(text: str) -> tuple[str | None, float | None, str | None, str]:
    m = STRENGTH_PATTERN.search(text)
    if not m:
        return None, None, None, text
    value = float(m.group(1))
    raw_unit = m.group(2).lower()
    unit = UNIT_DISPLAY.get(raw_unit, raw_unit)
    display = f"{value:g}%" if unit == "%" else f"{value:g} {unit}"
    return display, value, unit, _cut(text, *m.span())


def _parse_slots(text: str) -> tuple[str | None, list[float], str]:
    m = SLOT_PATTERN.search(text)
    if not m:
        return None, [], text
    toks = [g for g in m.groups() if g is not None]
    if len(toks) < 2:
        return None, [], text
    values = [slot_value(t) for t in toks]
    if not any(values):  # "0-0-0" is not a real schedule
        return None, [], text
    return m.group(0).strip(), values, _cut(text, *m.span())


def _parse_dose_amount(text: str) -> tuple[str | None, float | None, str | None, str]:
    m = DOSE_AMOUNT.search(text)
    if not m:
        return None, None, None, text
    qty = slot_value(m.group(1))
    noun = _AMOUNT_NOUN.get(m.group(2).lower(), m.group(2).lower())
    display = _fmt_amount(qty, noun)
    return display, qty, noun, _cut(text, *m.span())


def _fmt_amount(qty: float, noun: str) -> str:
    plural = noun
    if qty > 1 and float(qty).is_integer() and not noun.endswith(("l", ")")):
        plural = noun + "s"
    return f"{fmt_qty(qty)} {plural}"


def _scan_sig(text: str) -> tuple[dict[str, object], str]:
    """Pull PRN/STAT flags, then multi-word sig phrases, then short sig codes."""
    found: dict[str, object] = {}
    out = text


    m = PRN_RE.search(out)
    if m:
        found["prn"] = True
        out = _cut(out, *m.span())
    m = STAT_RE.search(out)
    if m:
        found["stat"] = True
        out = _cut(out, *m.span())

    # 2. day of the week ("once a week on Sunday")
    m = DAY_OF_WEEK_RE.search(out)
    if m:
        tok = m.group(1).lower()
        found["day_of_week"] = DAY_FULL.get(tok[:3], tok.capitalize())
        out = _cut(out, *m.span())

    # 3. spelled-out frequency, priority order
    for pat, phrase, per_day in FREQUENCY_PHRASES:
        m = pat.search(out)
        if not m:
            continue
        if "{n}" in phrase:
            n = float(m.group(1))
            if "hours" in phrase:
                human, per_day = f"every {n:g} hours", (24 / n if n else None)
            else:
                human, per_day = f"{n:g} times a day", n
        else:
            human = phrase
        found["frequency_code"] = m.group(0).strip()
        found["frequency_phrase"] = human
        found["times_per_day"] = per_day
        out = _cut(out, *m.span())
        break

    # 4. spelled-out timing
    for pat, human in TIMING_PHRASES:
        m = pat.search(out)
        if not m:
            continue
        found["timing"] = human
        out = _cut(out, *m.span())
        break

  
    matches: list[tuple[str, re.Match[str]]] = []
    for token in sorted(
        set(FREQUENCIES) | set(TIMINGS) | set(ROUTES), key=len, reverse=True
    ):
        esc = re.escape(token).replace(r"\.", r"\.?")
        pat = re.compile(rf"(?<![\w/]){esc}(?![\w/])", re.IGNORECASE)
        m = pat.search(out)
        if m:
            matches.append((norm_key(token), m))

    def _take_frequency(key: str, m: re.Match[str]) -> None:
        phrase, per_day = FREQUENCIES[key]
        found["frequency_code"] = m.group(0).upper()
        found["frequency_phrase"] = phrase
        found["times_per_day"] = per_day

    # pass 1: codes that can only mean one thing
    for key, m in matches:
        if key in FREQUENCIES and key not in TIMINGS and "frequency_code" not in found:
            _take_frequency(key, m)
        elif key in TIMINGS and key not in FREQUENCIES and "timing" not in found:
            found["timing"] = TIMINGS[key]
        elif key in ROUTES and key not in FREQUENCIES and "route" not in found:
            found["route"] = ROUTES[key]

    # pass 2: codes that could be either, into whichever slot is still empty
    for key, m in matches:
        if key in FREQUENCIES and key in TIMINGS:
            if "frequency_code" not in found:
                _take_frequency(key, m)
            elif "timing" not in found:
                found["timing"] = TIMINGS[key]
        elif key in ROUTES and "route" not in found:
            found["route"] = ROUTES[key]

    for _key, m in matches:
        out = _cut(out, *m.span())
    return found, out


def _clean_name(residual: str) -> str:
    txt = FORM_SUFFIX.sub(" ", residual)
    txt = re.sub(r"[()\[\]{}]", " ", txt)
    txt = NAME_NOISE.sub(" ", txt)
    txt = re.sub(r"[,;:/\\|]+", " ", txt)
    txt = re.sub(r"[-–—]{2,}", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip(" .-")
    toks = [t for t in txt.split(" ") if t]
    keep: list[str] = []
    for t in toks:
        if t.replace(".", "").isdigit() and keep:
            keep.append(t)          # "Dolo 650" - the number belongs to the brand
        elif len(t) == 1 and not t.isalpha():
            continue
        elif len(t) == 1 and keep:
            continue
        else:
            keep.append(t)
    return " ".join(keep).strip(" .-")


def _split_bare_dose_count(name: str) -> tuple[str, float | None]:
    """'Becosules 1' -> ('Becosules', 1.0); 'Dolo 650' -> ('Dolo 650', None)."""
    m = re.search(r"^(.*\S)\s+(\d+(?:\.\d+)?)$", name)
    if not m:
        return name, None
    head, num = m.group(1), float(m.group(2))
    if num <= _MAX_BARE_DOSE_COUNT and any(c.isalpha() for c in head):
        return head, num
    return name, None


def parse_line(line: str) -> Medication | None:
    """Parse one prescription line into a Medication, or ``None`` if it is not one."""
    raw = line.strip()
    if not raw:
        return None
    work = LEADING_JUNK.sub("", raw).strip()
    if not work or SECTION_HEADERS.match(work):
        return None

    med = Medication(raw=raw)

    # 1. dosage form written as a prefix ("T.", "Cap", "Syp")
    unit_noun = "tablet"
    fm = FORM_PREFIX.match(work)
    if fm:
        key = norm_key(fm.group(1))
        form_name, unit_noun = DOSAGE_FORMS.get(key, ("tablet", "tablet"))
        med.form = form_name
        work = work[fm.end():]
    else:
        fs = FORM_SUFFIX.search(work)
        if fs:
            key = norm_key(fs.group(1)).rstrip("s")
            form_name, unit_noun = DOSAGE_FORMS.get(key, (key, key))
            med.form = form_name

    # 2. trailing indication ("... SOS for knee pain"), captured before the text
    #    is chopped up, since the pattern is anchored to the end of the line.
    indication: str | None = None
    im = INDICATION_RE.search(work)
    if im:
        candidate = im.group(1).strip()
        if candidate and not any(ch.isdigit() for ch in candidate):
            indication = candidate
            work = _cut(work, *im.span())
    if indication is None:
        # bare symptom next to an as-needed dose ("Syp Crocin 5 ml QID SOS fever")
        sm = SYMPTOM_RE.search(work)
        if sm:
            indication = sm.group(1).lower()
            work = _cut(work, *sm.span())

    # 3. structured pieces, in an order that prevents cross-matching
    work = PER_VOLUME.sub(_BLANK, work)
    work = PATIENT_NOISE.sub(_BLANK, work)
    med.strength, med.strength_value, med.strength_unit, work = _parse_strength(work)
    med.dose_pattern, slots, work = _parse_slots(work)
    med.duration, work = _parse_duration(work)
    amount_display, amount_qty, amount_noun, work = _parse_dose_amount(work)
    sig, work = _scan_sig(work)

    if (
        amount_qty is None
        and med.strength_value
        and med.strength_unit in ("ml", "unit", "units")
    ):
        noun = "ml" if med.strength_unit == "ml" else "unit"
        amount_qty, amount_noun = med.strength_value, noun
        amount_display = _fmt_amount(med.strength_value, noun)
        med.strength = None
        med.strength_value = None
        med.strength_unit = None

    # 4. name is whatever survives
    name = _clean_name(work)
    if amount_qty is None:
        name, bare = _split_bare_dose_count(name)
        if bare is not None:
            amount_qty, amount_noun = bare, unit_noun
            amount_display = _fmt_amount(bare, unit_noun)
    med.name = name or None

    # 5. resolve against the local knowledge base
    hit = drugkb.resolve(med.name)
    if hit:
        key, entry, kind, score = hit
        med.generic = entry["generic"]
        med.purpose = entry["purpose"]
        med.notes.append(f"kb:{key}:{kind}:{score}")
        if not med.form and entry["dose_unit"] in ("puff", "drop", "ml", "application"):
            unit_noun = entry["dose_unit"]
        if med.strength_value is None and med.name:
            # "Dolo 650" / "Telma 40": a bare number on a known brand means mg
            tail = re.search(r"(\d+(?:\.\d+)?)\s*$", med.name)
            if tail and entry["dose_unit"] == "mg":
                med.strength_value = float(tail.group(1))
                med.strength_unit = "mg"
                med.strength = f"{med.strength_value:g} mg"
                med.notes.append("strength inferred from brand name")

    # 6. schedule in plain English
    if slots:
        med.frequency_code = med.dose_pattern
        med.frequency_human = describe_slots(slots, amount_noun or unit_noun)
        med.times_per_day = float(sum(1 for v in slots if v > 0))
        med.units_per_day = total_daily_units(slots)
    elif sig.get("frequency_code"):
        med.frequency_code = str(sig["frequency_code"])
        med.frequency_human = str(sig["frequency_phrase"])
        tpd = sig.get("times_per_day")
        med.times_per_day = float(tpd) if isinstance(tpd, (int, float)) else None
        if med.times_per_day:
            med.units_per_day = med.times_per_day * (amount_qty or 1.0)

    med.dose_amount = amount_display
    med.route = sig.get("route") if isinstance(sig.get("route"), str) else None
    med.timing = sig.get("timing") if isinstance(sig.get("timing"), str) else None
    med.prn = bool(sig.get("prn"))
    if sig.get("stat"):
        med.notes.append("single immediate dose (STAT)")
    if sig.get("day_of_week"):
        med.notes.append(f"on {sig['day_of_week']}")
    if indication:
        med.notes.append(f"indication: {indication}")

    # 7. decide whether this really was a medication line
    structured = any(
        (med.strength, med.dose_pattern, med.frequency_code, med.form, med.duration)
    )
    plausible_name = bool(med.name) and len(med.name) >= 3 and any(
        c.isalpha() for c in med.name
    )
    if not plausible_name:
        return None
    if not (hit is not None or structured):
        return None
    return med



RECORD_SEP = re.compile(r"\s+/\s+")


def _split_records(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        out.extend(RECORD_SEP.split(line))
    return out


def parse(text: str) -> ParsedPrescription:
    """Parse a whole prescription block."""
    result = ParsedPrescription(raw_text=text)
    for line in _split_records(text):
        stripped = line.strip()
        if not stripped:
            continue
        if SECTION_HEADERS.match(LEADING_JUNK.sub("", stripped)):
            continue
        if _looks_like_instruction(stripped):
            result.general_instructions.append(LEADING_JUNK.sub("", stripped))
            continue
        med = parse_line(stripped)
        if med is not None:
            result.medications.append(med)
        else:
            result.unparsed_lines.append(stripped)
    return result


def parsed_summary(p: ParsedPrescription) -> str:
    """Compact text rendering of the parse, used as grounding context in prompts."""
    lines: list[str] = []
    for i, m in enumerate(p.medications, 1):
        bits = [f"{i}. name={m.name!r}"]
        for field in (
            "generic", "strength", "form", "dose_amount", "dose_pattern",
            "frequency_code", "frequency_human", "timing", "route", "duration",
        ):
            val = getattr(m, field)
            if val:
                bits.append(f"{field}={val!r}")
        if m.prn:
            bits.append("prn=True")
        if m.units_per_day:
            bits.append(f"units_per_day={m.units_per_day:g}")
        extra = [n for n in m.notes if not n.startswith("kb:")]
        if extra:
            bits.append("notes=" + "; ".join(extra))
        lines.append("   ".join(bits))
    if p.general_instructions:
        lines.append("general_instructions: " + " | ".join(p.general_instructions))
    if p.unparsed_lines:
        lines.append("UNPARSED: " + " | ".join(p.unparsed_lines))
    return "\n".join(lines) if lines else "(nothing parsed)"
