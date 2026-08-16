

from __future__ import annotations

import re

# ------------------------------------------------------------------ forms
# Written prefix -> (human form, default unit noun)
DOSAGE_FORMS: dict[str, tuple[str, str]] = {
    "t": ("tablet", "tablet"),
    "tb": ("tablet", "tablet"),
    "tab": ("tablet", "tablet"),
    "tabs": ("tablet", "tablet"),
    "tablet": ("tablet", "tablet"),
    "cap": ("capsule", "capsule"),
    "caps": ("capsule", "capsule"),
    "capsule": ("capsule", "capsule"),
    "syp": ("syrup", "ml"),
    "syr": ("syrup", "ml"),
    "syrup": ("syrup", "ml"),
    "susp": ("suspension", "ml"),
    "suspension": ("suspension", "ml"),
    "elixir": ("elixir", "ml"),
    "inj": ("injection", "dose"),
    "injection": ("injection", "dose"),
    "oint": ("ointment", "application"),
    "ointment": ("ointment", "application"),
    "cream": ("cream", "application"),
    "gel": ("gel", "application"),
    "lotion": ("lotion", "application"),
    "powder": ("powder", "dose"),
    "sac": ("sachet", "sachet"),
    "sachet": ("sachet", "sachet"),
    "drop": ("drops", "drop"),
    "drops": ("drops", "drop"),
    "ed": ("eye drops", "drop"),
    "e/d": ("eye drops", "drop"),
    "eyedrops": ("eye drops", "drop"),
    "eardrops": ("ear drops", "drop"),
    "nd": ("nasal drops", "drop"),
    "n/d": ("nasal drops", "drop"),
    "neb": ("nebulisation", "dose"),
    "inh": ("inhaler", "puff"),
    "inhaler": ("inhaler", "puff"),
    "mdi": ("inhaler", "puff"),
    "rotacap": ("rotacap", "capsule"),
    "supp": ("suppository", "suppository"),
    "pess": ("pessary", "pessary"),
    "patch": ("patch", "patch"),
    "spray": ("spray", "spray"),
    "gargle": ("gargle", "dose"),
    "mw": ("mouthwash", "dose"),
    "lozenge": ("lozenge", "lozenge"),
    "shampoo": ("shampoo", "application"),
    "soap": ("soap", "application"),
    "iv": ("intravenous infusion", "dose"),
    "vial": ("vial", "dose"),
}

# ------------------------------------------------------------- frequencies
# code -> (plain-English phrase, doses per day or None when as-needed/irregular)
FREQUENCIES: dict[str, tuple[str, float | None]] = {
    "od": ("once a day", 1),
    "o.d": ("once a day", 1),
    "1od": ("once a day", 1),
    "sid": ("once a day", 1),
    "qd": ("once a day", 1),
    "q.d": ("once a day", 1),
    "daily": ("once a day", 1),
    "bd": ("twice a day", 2),
    "b.d": ("twice a day", 2),
    "bid": ("twice a day", 2),
    "b.i.d": ("twice a day", 2),
    "bds": ("twice a day", 2),
    "twice": ("twice a day", 2),
    "tds": ("three times a day", 3),
    "t.d.s": ("three times a day", 3),
    "tid": ("three times a day", 3),
    "t.i.d": ("three times a day", 3),
    "thrice": ("three times a day", 3),
    "qid": ("four times a day", 4),
    "q.i.d": ("four times a day", 4),
    "qds": ("four times a day", 4),
    "q.d.s": ("four times a day", 4),
    "5td": ("five times a day", 5),
    "hs": ("at bedtime", 1),
    "h.s": ("at bedtime", 1),
    "nocte": ("at night", 1),
    "on": ("at night", 1),
    "om": ("in the morning", 1),
    "mane": ("in the morning", 1),
    "stat": ("immediately, as a single dose", None),
    "sos": ("only if needed", None),
    "s.o.s": ("only if needed", None),
    "prn": ("only when needed", None),
    "p.r.n": ("only when needed", None),
    "qod": ("every other day", 0.5),
    "eod": ("every other day", 0.5),
    "altdie": ("every other day", 0.5),
    "alt": ("every other day", 0.5),
    "ow": ("once a week", 1 / 7),
    "weekly": ("once a week", 1 / 7),
    "biw": ("twice a week", 2 / 7),
    "tiw": ("three times a week", 3 / 7),
    "q2h": ("every 2 hours", 12),
    "q3h": ("every 3 hours", 8),
    "q4h": ("every 4 hours", 6),
    "q6h": ("every 6 hours", 4),
    "q8h": ("every 8 hours", 3),
    "q12h": ("every 12 hours", 2),
    "q24h": ("every 24 hours", 1),
    "q48h": ("every 48 hours", 0.5),
}

# --------------------------------------------------------------- timing
TIMINGS: dict[str, str] = {
    "af": "after food",
    "a/f": "after food",
    "pc": "after meals",
    "p.c": "after meals",
    "postfood": "after food",
    "afterfood": "after food",
    "bf": "before food",
    "b/f": "before food",
    "ac": "before meals",
    "a.c": "before meals",
    "beforefood": "before food",
    "es": "on an empty stomach",
    "emptystomach": "on an empty stomach",
    "withfood": "with food",
    "withmilk": "with milk",
    "withwater": "with a full glass of water",
    "hs": "at bedtime",
    "beforesleep": "at bedtime",
    "beforebreakfast": "before breakfast",
    "bbf": "before breakfast",
    "abf": "after breakfast",
    "bbd": "before bed",
    "afterbreakfast": "after breakfast",
    "beforedinner": "before dinner",
    "afterdinner": "after dinner",
}

# ---------------------------------------------------------------- routes
ROUTES: dict[str, str] = {
    "po": "by mouth",
    "p.o": "by mouth",
    "peroral": "by mouth",
    "oral": "by mouth",
    "orally": "by mouth",
    "sl": "under the tongue",
    "s.l": "under the tongue",
    "sublingual": "under the tongue",
    "iv": "into a vein (drip or injection)",
    "i.v": "into a vein (drip or injection)",
    "im": "as an injection into a muscle",
    "i.m": "as an injection into a muscle",
    "sc": "as an injection under the skin",
    "s.c": "as an injection under the skin",
    "sq": "as an injection under the skin",
    "pr": "into the rectum",
    "p.r": "into the rectum",
    "pv": "into the vagina",
    "p.v": "into the vagina",
    "top": "on the skin",
    "topical": "on the skin",
    "topically": "on the skin",
    "la": "applied locally",
    "inhal": "breathed in through the mouth",
    "inhaled": "breathed in through the mouth",
    "nasal": "into the nose",
    "intranasal": "into the nose",
    "ophthalmic": "into the eye",
    "otic": "into the ear",
    "td": "as a skin patch",
    "transdermal": "as a skin patch",
    "neb": "breathed in using a nebuliser machine",
}

# ------------------------------------------------------- slot notation
# Slot labels for N-slot dose patterns like 1-0-1 or 1-1-1-1.
SLOT_LABELS_3 = ("morning", "afternoon", "night")
SLOT_LABELS_4 = ("morning", "afternoon", "evening", "night")
SLOT_LABELS_2 = ("morning", "night")

# A slot value: 1, 2, 0, ½, 1/2, 0.5, 1.5
_SLOT_TOKEN = r"(?:\d+(?:\.\d+)?(?:/\d+)?|½|¼|¾|0)"
SLOT_PATTERN = re.compile(
    rf"(?<![\w/])({_SLOT_TOKEN})\s*[-–—]\s*({_SLOT_TOKEN})"
    rf"(?:\s*[-–—]\s*({_SLOT_TOKEN}))?(?:\s*[-–—]\s*({_SLOT_TOKEN}))?(?![\w])"
)

_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75}

# ------------------------------------------------------------- durations
# Indian shorthand: 5/7 = 5 days, 2/52 = 2 weeks, 3/12 = 3 months
DURATION_SHORTHAND = re.compile(r"\b(\d+)\s*/\s*(7|52|12)\b")
DURATION_EXPLICIT = re.compile(
    r"(?:x|×|for)?\s*(\d+)\s*"
    r"(day|days|d|week|weeks|wk|wks|w|month|months|mon|mth|mths|m|year|years|yr|yrs)\b",
    re.IGNORECASE,
)
DURATION_WORDS = {
    "d": "day", "day": "day", "days": "day",
    "w": "week", "wk": "week", "wks": "week", "week": "week", "weeks": "week",
    "m": "month", "mon": "month", "mth": "month", "mths": "month",
    "month": "month", "months": "month",
    "yr": "year", "yrs": "year", "year": "year", "years": "year",
}

# ----------------------------------------------------------------- units
STRENGTH_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mg/ml|mcg/ml|iu/ml|mg|mcg|ug|µg|g|gm|ml|l|iu|units?|%|meq)\b",
    re.IGNORECASE,
)
UNIT_DISPLAY = {
    "mg": "mg", "mcg": "mcg", "ug": "mcg", "µg": "mcg",
    "g": "g", "gm": "g", "ml": "ml", "l": "litre",
    "iu": "IU", "unit": "units", "units": "units", "%": "%",
    "meq": "mEq", "mg/ml": "mg/ml", "mcg/ml": "mcg/ml", "iu/ml": "IU/ml",
}

# --------------------------------------------------- misc instruction cues
GENERAL_INSTRUCTION_CUES = (
    "review", "follow up", "f/u", "revisit", "plenty of water", "rest",
    "avoid", "diet", "exercise", "monitor", "check", "bp", "sugar",
    "investigation", "test", "scan", "x-ray", "usg", "report",
    "steam", "gargle", "warm", "salt water", "do not", "stop",
)



TIMING_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bon\s+an?\s+empty\s+stomach\b", re.I), "on an empty stomach"),
    (re.compile(r"\bempty\s+stomach\b", re.I), "on an empty stomach"),
    (re.compile(r"\bafter\s+breakfast\b", re.I), "after breakfast"),
    (re.compile(r"\bafter\s+lunch\b", re.I), "after lunch"),
    (re.compile(r"\bafter\s+dinner\b", re.I), "after dinner"),
    (re.compile(r"\bafter\s+(?:food|meals?|eating)\b", re.I), "after food"),
    (re.compile(r"\bbefore\s+breakfast\b", re.I), "before breakfast"),
    (re.compile(r"\bbefore\s+lunch\b", re.I), "before lunch"),
    (re.compile(r"\bbefore\s+dinner\b", re.I), "before dinner"),
    (re.compile(r"\bbefore\s+(?:food|meals?|eating)\b", re.I), "before food"),
    (re.compile(r"\bwith\s+plenty\s+of\s+water\b", re.I), "with a full glass of water"),
    (re.compile(r"\bwith\s+(?:food|meals?)\b", re.I), "with food"),
    (re.compile(r"\bwith\s+milk\b", re.I), "with milk"),
    (re.compile(r"\bwith\s+water\b", re.I), "with a full glass of water"),
    (re.compile(r"\b(?:at\s+)?bed\s*time\b", re.I), "at bedtime"),
    (re.compile(r"\bbefore\s+(?:sleep|sleeping|bed)\b", re.I), "at bedtime"),
    (re.compile(r"\bearly\s+morning\b", re.I), "early in the morning"),
    (re.compile(r"\bin\s+the\s+morning\b", re.I), "in the morning"),
    (re.compile(r"\bin\s+the\s+afternoon\b", re.I), "in the afternoon"),
    (re.compile(r"\bin\s+the\s+evening\b", re.I), "in the evening"),
    (re.compile(r"\bat\s+night\b", re.I), "at night"),
    (re.compile(r"\bmorning\b", re.I), "in the morning"),
    (re.compile(r"\bafternoon\b", re.I), "in the afternoon"),
    (re.compile(r"\bevening\b", re.I), "in the evening"),
    (re.compile(r"\bnights?\b", re.I), "at night"),
]

# (regex, plain-English phrase, doses per day)
FREQUENCY_PHRASES: list[tuple[re.Pattern[str], str, float | None]] = [
    (re.compile(r"\b(?:three\s+times|thrice)\s+(?:a\s+day|daily|per\s+day)\b", re.I),
     "three times a day", 3),
    (re.compile(r"\bfour\s+times\s+(?:a\s+day|daily|per\s+day)\b", re.I),
     "four times a day", 4),
    (re.compile(r"\btwice\s+(?:a\s+day|daily|per\s+day)\b", re.I), "twice a day", 2),
    (re.compile(r"\bonce\s+(?:a\s+day|daily|per\s+day|every\s+day)\b", re.I),
     "once a day", 1),
    (re.compile(r"\b(?:once\s+(?:a|per|every)\s+week|once\s+weekly|weekly\s+once|"
                r"one\s+tablet\s+weekly)\b", re.I), "once a week", 1 / 7),
    (re.compile(r"\btwice\s+(?:a|per)\s+week\b", re.I), "twice a week", 2 / 7),
    (re.compile(r"\bthree\s+times\s+(?:a|per)\s+week\b", re.I), "three times a week", 3 / 7),
    (re.compile(r"\bonce\s+(?:a|per|every)\s+month\b", re.I), "once a month", 1 / 30),
    (re.compile(r"\b(?:every\s+other\s+day|on\s+alternate\s+days?|alternate\s+days?|"
                r"alternate\s+day)\b", re.I), "every other day", 0.5),
    (re.compile(r"\bevery\s+(\d+)\s*(?:hours?|hrs?|h)\b", re.I), "every {n} hours", None),
    (re.compile(r"\b(\d+)\s*(?:times|x)\s+(?:a\s+day|daily|per\s+day)\b", re.I),
     "{n} times a day", None),
]

PRN_RE = re.compile(r"\b(?:s\.?o\.?s|p\.?r\.?n|as\s+(?:and\s+when\s+)?needed|"
                    r"if\s+(?:needed|required)|when\s+(?:needed|required)|"
                    r"only\s+if\s+(?:needed|required))\b", re.I)
STAT_RE = re.compile(r"\b(?:stat|immediately|at\s+once)\b", re.I)
DAY_OF_WEEK_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", re.I)
# "for pain", "for fever", "SOS for headache"
INDICATION_RE = re.compile(r"\bfor\s+([a-z][a-z\s]{2,28}?)\s*$", re.I)

# Symptoms written bare next to an as-needed dose ("SOS fever", "QID cough").
SYMPTOM_RE = re.compile(
    r"\b(fever|pain|headache|bodyache|body\s+ache|cough|cold|sneezing|vomiting|"
    r"nausea|acidity|gas|bloating|giddiness|dizziness|itching|rash|swelling|"
    r"loose\s+motions?|diarrh?oea|constipation|cramps?|spasms?|burning|"
    r"breathlessness|wheez(?:e|ing)|insomnia|sleeplessness|anxiety)\b",
    re.I,
)

DAY_FULL = {
    "mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
    "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}


def norm_key(token: str) -> str:
    """Normalise a token for dictionary lookup: lowercase, strip trailing dots."""
    t = token.strip().lower()
    t = t.replace(" ", "")
    while t.endswith("."):
        t = t[:-1]
    return t


def slot_value(tok: str) -> float:
    """Parse one slot token (``1``, ``0``, ``1/2``, ``½``, ``0.5``) to a number."""
    tok = tok.strip()
    if tok in _FRACTIONS:
        return _FRACTIONS[tok]
    if "/" in tok:
        num, _, den = tok.partition("/")
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(tok)
    except ValueError:
        return 0.0


def fmt_qty(value: float) -> str:
    """Format a dose quantity the way a patient would read it."""
    if value == 0:
        return "0"
    if abs(value - 0.5) < 1e-9:
        return "half"
    if abs(value - 0.25) < 1e-9:
        return "quarter"
    if abs(value - 0.75) < 1e-9:
        return "three-quarters of"
    if abs(value - 1.5) < 1e-9:
        return "one and a half"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def describe_slots(values: list[float], unit_noun: str = "tablet") -> str:
    """Turn ``[1, 0, 1]`` into 'morning and night' style plain English."""
    labels = {2: SLOT_LABELS_2, 3: SLOT_LABELS_3, 4: SLOT_LABELS_4}.get(
        len(values), SLOT_LABELS_3
    )
    parts: list[str] = []
    for label, val in zip(labels, values):
        if val and val > 0:
            qty = fmt_qty(val)
            noun = unit_noun
            if unit_noun in ("tablet", "capsule", "sachet", "suppository", "pessary"):
                if val > 1 and float(val).is_integer():
                    noun = unit_noun + "s"
            parts.append(f"{qty} {noun} in the {label}" if label != "night"
                         else f"{qty} {noun} at night")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def total_daily_units(values: list[float]) -> float:
    return float(sum(values))
