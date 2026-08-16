

from __future__ import annotations

from . import drugkb
from .schema import Medication, ParsedPrescription, SafetyFlag

SEVERITY_ORDER = {"info": 0, "caution": 1, "warning": 2, "critical": 3}


DUPLICATE_GROUPS: dict[str, tuple[str, ...]] = {
    "NSAID painkiller": (
        "ibuprofen", "diclofenac", "aceclofenac", "naproxen", "mefenamic acid",
    ),
    "acid-reducing tablet": ("omeprazole", "pantoprazole", "rabeprazole"),
    "statin cholesterol tablet": ("atorvastatin", "rosuvastatin"),
    "antihistamine": ("cetirizine", "levocetirizine"),
    "blood thinner": ("aspirin", "clopidogrel", "warfarin"),
    "uric acid tablet": ("allopurinol", "febuxostat"),
    "fluoroquinolone antibiotic": (
        "ciprofloxacin", "levofloxacin", "ofloxacin", "norfloxacin",
    ),
    "benzodiazepine sedative": ("alprazolam", "clonazepam"),
    "antidepressant": ("sertraline", "escitalopram", "amitriptyline"),
    "blood pressure tablet acting on the same hormone": (
        "telmisartan", "losartan", "enalapril", "ramipril",
    ),
}

# Pairs that need an explicit patient-facing message beyond "these interact".
PAIR_RULES: list[tuple[str, str, str, str]] = [
    ("warfarin", "*nsaid*", "warning",
     "You are on the blood thinner warfarin together with a painkiller of the "
     "ibuprofen/diclofenac family. This combination clearly raises the risk of "
     "stomach and internal bleeding. Please confirm with your doctor before "
     "taking the painkiller, and ask whether paracetamol would do instead."),
    ("aspirin", "*nsaid*", "caution",
     "You are taking low-dose aspirin along with another anti-inflammatory "
     "painkiller. Together they irritate the stomach much more. Take both after "
     "food and tell your doctor about any stomach pain, black stools or "
     "blood in vomit."),
    ("clopidogrel", "omeprazole", "caution",
     "Omeprazole can weaken how well clopidogrel protects you from clots. "
     "Doctors usually prefer pantoprazole with clopidogrel. Please ask your "
     "doctor to confirm which acid tablet you should be on."),
    ("methotrexate", "*nsaid*", "warning",
     "Anti-inflammatory painkillers slow down how your body clears methotrexate, "
     "which can push its level into the toxic range. Do not take them together "
     "without your doctor's explicit approval."),
    ("levothyroxine", "calcium-vitamin-d3", "caution",
     "Calcium blocks thyroid tablets from being absorbed. Take your thyroid "
     "tablet first thing in the morning on an empty stomach, and keep the "
     "calcium at least 4 hours later."),
    ("levothyroxine", "iron-folic-acid", "caution",
     "Iron blocks thyroid tablets from being absorbed. Keep the thyroid tablet "
     "and the iron tablet at least 4 hours apart."),
    ("iron-folic-acid", "*quinolone*", "caution",
     "Iron blocks this antibiotic from being absorbed, so it may not work. Keep "
     "the antibiotic and the iron tablet at least 2 hours apart, and keep milk, "
     "antacids and calcium away from the antibiotic too."),
    ("calcium-vitamin-d3", "*quinolone*", "caution",
     "Calcium blocks this antibiotic from being absorbed, so it may not work. "
     "Keep the antibiotic and the calcium tablet at least 2 hours apart."),
    ("spironolactone", "*potassium_raiser*", "warning",
     "Both of these can raise the potassium level in your blood, and a high "
     "potassium level can upset your heart rhythm. Make sure your doctor has "
     "ordered the blood test to check it."),
    ("metronidazole", "alcohol", "warning",
     "Do not drink any alcohol during this course or for 3 days after it. With "
     "metronidazole, alcohol causes severe flushing, vomiting and headache."),
]

NSAIDS = DUPLICATE_GROUPS["NSAID painkiller"]
POTASSIUM_RAISERS = ("telmisartan", "losartan", "enalapril", "ramipril")
QUINOLONES = DUPLICATE_GROUPS["fluoroquinolone antibiotic"]

# Drugs where taking the medicine on the wrong SCHEDULE is the main danger.
SCHEDULE_RULES: dict[str, dict[str, object]] = {
    "methotrexate": {
        "expect": "weekly",
        # The highest severity in the system: daily methotrexate is a well
        # documented fatal dispensing error, not merely something to mention.
        "severity": "critical",
        "message": (
            "Methotrexate for arthritis or psoriasis is a ONCE A WEEK tablet. "
            "Taking it every day by mistake can cause life-threatening harm. "
            "This prescription does not clearly say 'once a week' - please "
            "confirm the exact day with your doctor before you start."
        ),
    },
}


PREGNANCY_ALERT = (
    "warfarin", "methotrexate", "doxycycline", "ciprofloxacin", "levofloxacin",
    "ofloxacin", "norfloxacin", "telmisartan", "losartan", "enalapril",
    "ramipril", "carbamazepine", "phenytoin", "atorvastatin", "rosuvastatin",
    "fluconazole", "albendazole", "spironolactone", "tramadol",
)

# Mass-unit conversion factors into a common base of micrograms.
_TO_MCG = {"mcg": 1.0, "mg": 1000.0, "g": 1_000_000.0}


def _kb_key(med: Medication) -> str | None:
    for note in med.notes:
        if note.startswith("kb:"):
            return note.split(":")[1]
    return None


def daily_dose(med: Medication, entry: dict) -> tuple[float, str] | None:
    """Total dose per day, expressed in the KB's own ``dose_unit``.

    Returns ``None`` when the prescription does not carry enough information
    (no strength, or no schedule) to do the arithmetic honestly.
    """
    if not med.units_per_day:
        return None
    kb_unit = entry.get("dose_unit") or ""

    if kb_unit in _TO_MCG:
        if med.strength_value is None or med.strength_unit not in _TO_MCG:
            return None
        total_mcg = med.strength_value * _TO_MCG[med.strength_unit] * med.units_per_day
        return total_mcg / _TO_MCG[kb_unit], kb_unit

    # Countable units: puffs, drops, ml, capsules, applications, insulin units.
    if med.strength_unit in ("ml", "IU", "units") and kb_unit in ("ml", "units"):
        if med.strength_value is None:
            return None
        return med.strength_value * med.units_per_day, kb_unit
    return med.units_per_day, kb_unit


def _fmt(value: float) -> str:
    return f"{value:g}"


def check(parsed: ParsedPrescription) -> list[SafetyFlag]:
    """Run every rule over a parsed prescription and return the flags raised."""
    flags: list[SafetyFlag] = []
    resolved: list[tuple[str, dict, Medication]] = []
    for med in parsed.medications:
        key = _kb_key(med)
        entry = drugkb.get(key) if key else None
        if key and entry:
            resolved.append((key, entry, med))

    present = {key for key, _, _ in resolved}

    # ---------------------------------------------------------- dose ceilings
    for key, entry, med in resolved:
        ceiling = entry.get("max_daily_dose")
        if not ceiling:
            continue
        dd = daily_dose(med, entry)
        if dd is None:
            continue
        total, unit = dd
        if total > float(ceiling) * 1.001:
            flags.append(SafetyFlag(
                kind="max_dose",
                severity="warning",
                message=(
                    f"As written, this adds up to {_fmt(total)} {unit} of "
                    f"{entry['generic']} per day, which is above the usual adult "
                    f"maximum of {_fmt(float(ceiling))} {unit} a day. Please get "
                    f"this checked with your doctor or pharmacist before you "
                    f"start, and do not simply take less on your own."
                ),
                subjects=[med.name or key],
            ))

    # ------------------------------------------------------ duplicate therapy
    for label, members in DUPLICATE_GROUPS.items():
        hits = [(k, m) for k, _, m in resolved if k in members]
        if len(hits) < 2:
            continue
        names = [m.name or k for k, m in hits]
        if label == "blood thinner":
            flags.append(SafetyFlag(
                kind="duplicate",
                severity="caution",
                message=(
                    f"This prescription has more than one blood thinner "
                    f"({', '.join(names)}). Doctors do prescribe these together "
                    f"on purpose after a stent or a heart attack, but it raises "
                    f"bleeding risk. Confirm with your doctor that both are "
                    f"meant to be taken, and report any unusual bleeding, "
                    f"black stools or blood in vomit at once."
                ),
                subjects=names,
            ))
        else:
            flags.append(SafetyFlag(
                kind="duplicate",
                severity="caution",
                message=(
                    f"{' and '.join(names)} are both a {label}. Taking two of the "
                    f"same type together usually adds side effects without adding "
                    f"benefit. Please check with your doctor or pharmacist whether "
                    f"you are meant to take both."
                ),
                subjects=names,
            ))

    # ------------------------------------------------------------ named pairs
    # covered_pairs holds KB-key pairs already explained by a specific rule, so
    # the generic fallback below does not repeat them in vaguer words.
    covered_pairs: set[frozenset[str]] = set()
    for a, b, severity, message in PAIR_RULES:
        subjects, covered = _match_pair(a, b, resolved)
        if subjects:
            flags.append(SafetyFlag(
                kind="interaction", severity=severity,
                message=message, subjects=subjects,
            ))
            covered_pairs |= covered

    # ------------------------------------------- generic KB interaction pairs
    seen_pairs: set[frozenset[str]] = set()
    for key_a, entry_a, med_a in resolved:
        for partner in entry_a.get("interacts_with", []):
            pkey = partner.lower()
            if pkey not in present or pkey == key_a:
                continue
            pair = frozenset((key_a, pkey))
            if pair in seen_pairs or pair in covered_pairs:
                continue
            seen_pairs.add(pair)
            other = next(e for k, e, _ in resolved if k == pkey)
            flags.append(SafetyFlag(
                kind="interaction",
                severity="caution",
                message=(
                    f"{entry_a['generic']} and {other['generic']} can affect each "
                    f"other. Your doctor may well have intended this, but mention "
                    f"to your pharmacist that you take both, and report any new "
                    f"or unusual symptom."
                ),
                subjects=[med_a.name or key_a, pkey],
            ))

    # -------------------------------------------------------- schedule rules
    for key, entry, med in resolved:
        rule = SCHEDULE_RULES.get(key)
        if not rule:
            continue
        freq = (med.frequency_human or "").lower()
        if rule["expect"] == "weekly" and "week" not in freq:
            flags.append(SafetyFlag(
                kind="schedule", severity=str(rule["severity"]),
                message=str(rule["message"]), subjects=[med.name or key],
            ))

    # --------------------------------------------------- high-risk monitoring
    for key, entry, med in resolved:
        if not entry.get("high_risk"):
            continue
        flags.append(SafetyFlag(
            kind="high_risk",
            severity="caution",
            message=(
                f"{entry['generic']} needs care to use safely - "
                f"{entry['serious_side_effects'][0]} is one of the things to "
                f"watch for. Follow the dose exactly, keep your review "
                f"appointments and any blood tests, and do not stop or change "
                f"it on your own."
            ),
            subjects=[med.name or key],
        ))

    # -------------------------------------------------------- pregnancy alert
    preg = [(k, m) for k, _, m in resolved if k in PREGNANCY_ALERT]
    if preg:
        names = [m.name or k for k, m in preg]
        flags.append(SafetyFlag(
            kind="high_risk",
            severity="caution",
            message=(
                f"{', '.join(names)} can harm an unborn baby or is not advised in "
                f"pregnancy. If you are pregnant, might be pregnant, are trying "
                f"to conceive, or are breastfeeding, tell your doctor before you "
                f"take the first dose."
            ),
            subjects=names,
        ))

    # ----------------------------------------------------- missing directions
    for med in parsed.medications:
        gaps: list[str] = []
        if not med.frequency_human:
            gaps.append("how often to take it")
        if not med.strength and not med.dose_amount:
            gaps.append("what dose to take")
        if gaps:
            flags.append(SafetyFlag(
                kind="ambiguous",
                severity="info",
                message=(
                    f"For {med.name or 'one of these medicines'}, the "
                    f"prescription does not clearly state {' or '.join(gaps)}. "
                    f"Ask your doctor or pharmacist to write it down before you "
                    f"start, rather than guessing."
                ),
                subjects=[med.name or "unknown"],
            ))
    if parsed.unparsed_lines:
        flags.append(SafetyFlag(
            kind="ambiguous",
            severity="info",
            message=(
                f"{len(parsed.unparsed_lines)} line(s) of this prescription could "
                f"not be read reliably. Please have your pharmacist read the "
                f"original prescription with you so nothing is missed."
            ),
            subjects=parsed.unparsed_lines[:3],
        ))

    flags.sort(key=lambda f: -SEVERITY_ORDER.get(f.severity, 0))
    return flags


def _match_pair(
    a: str, b: str, resolved: list[tuple[str, dict, Medication]],
) -> tuple[list[str], set[frozenset[str]]]:
    """Resolve a PAIR_RULES entry, expanding the ``*group*`` wildcards.

    Returns the patient-facing subject names plus the set of KB-key pairs this
    rule has now explained.
    """
    present = {k: m for k, _, m in resolved}
    if a not in present:
        return [], set()
    a_name = present[a].name or a

    group = None
    if b == "*nsaid*":
        group = NSAIDS
    elif b == "*potassium_raiser*":
        group = POTASSIUM_RAISERS
    elif b == "*quinolone*":
        group = QUINOLONES

    if group is not None:
        keys = [k for k in group if k in present and k != a]
        if not keys:
            return [], set()
        names = [present[k].name or k for k in keys]
        return [a_name, *names], {frozenset((a, k)) for k in keys}

    if b == "alcohol":
        # Advisory rule: alcohol is not a prescribed drug, so there is no KB
        # pair to suppress. Always worth stating for this medicine.
        return [a_name, "alcohol"], set()

    if b in present:
        return [a_name, present[b].name or b], {frozenset((a, b))}
    return [], set()


def format_flags(flags: list[SafetyFlag]) -> str:
    """Render flags as the patient-facing 'Important safety points' block."""
    if not flags:
        return ""
    icon = {"critical": "!!!", "warning": "!!", "caution": "!", "info": "i"}
    lines = ["Important safety points", "-" * 23]
    for f in flags:
        lines.append(f"[{icon.get(f.severity, '-')}] {f.message}")
    return "\n".join(lines)


def worst_severity(flags: list[SafetyFlag]) -> str | None:
    if not flags:
        return None
    return max(flags, key=lambda f: SEVERITY_ORDER.get(f.severity, 0)).severity
