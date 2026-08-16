

from __future__ import annotations

import pytest

from rxexplain.parser_rules import parse
from rxexplain.safety import check, worst_severity


def kinds(text: str) -> list[str]:
    return [f.kind for f in check(parse(text))]


def messages(text: str) -> str:
    return " ".join(f.message.lower() for f in check(parse(text)))


# ------------------------------------------------------------- dose ceilings
def test_paracetamol_overdose_is_flagged():
    flags = check(parse("Tab Paracetamol 650mg 2 tabs QID x 3 days"))
    assert any(f.kind == "max_dose" for f in flags)
    text = " ".join(f.message for f in flags)
    assert "5200" in text and "4000" in text


def test_paracetamol_normal_dose_is_not_flagged():
    assert "max_dose" not in kinds("T. Dolo 650 1-0-1 x 5 days AF")


def test_max_dose_message_tells_the_patient_to_check_not_to_self_adjust():
    msg = messages("Tab Paracetamol 650mg 2 tabs QID x 3 days")
    assert "doctor" in msg or "pharmacist" in msg
    assert "do not simply take less" in msg


# ------------------------------------------------------------- interactions
def test_warfarin_plus_nsaid():
    msg = messages("T Warf 5mg 0-0-1\nTab Brufen 400mg TDS x 5 days")
    assert "interaction" in kinds("T Warf 5mg 0-0-1\nTab Brufen 400mg TDS x 5 days")
    assert "bleed" in msg


def test_clopidogrel_plus_omeprazole():
    text = "Tab Clopilet 75mg OD\nCap Omez 20mg OD BF"
    assert "interaction" in kinds(text)
    assert "pantoprazole" in messages(text)


def test_levothyroxine_plus_calcium_spacing():
    text = "Tab Eltroxin 75mcg OD empty stomach\nT. Shelcal 500 1-0-1 AF"
    msg = messages(text)
    assert "interaction" in kinds(text)
    assert "4 hours" in msg or "apart" in msg


def test_arb_plus_potassium_sparing_diuretic():
    text = "Tab Telma 40 OD\nTab Aldactone 25mg OD"
    assert "interaction" in kinds(text)
    assert "potassium" in messages(text)


def test_quinolone_plus_iron_spacing():
    text = "T. Ciplox 500 BD x 7 days\nT. Livogen 1-0-0 AF"
    assert "interaction" in kinds(text)
    assert "2 hours" in messages(text) or "apart" in messages(text)


def test_metronidazole_alcohol_advice_is_present():
    assert "alcohol" in messages("Tab Metrogyl 400mg TDS x 5 days")


def test_interaction_is_reported_once_not_twice():
    # A specific named rule and the generic KB pair rule both match warfarin +
    # ibuprofen; the patient must not see the same warning twice.
    flags = check(parse("T Warf 5mg 0-0-1\nTab Brufen 400mg TDS x 5 days"))
    interactions = [f for f in flags if f.kind == "interaction"]
    assert len(interactions) == 1, [f.message for f in interactions]


def test_unrelated_drugs_produce_no_interaction():
    assert "interaction" not in kinds("T. Dolo 650 1-0-1 x 3 days\nSyp Ascoril 5ml TDS")


# --------------------------------------------------------- duplicate therapy
def test_two_nsaids_are_flagged_as_duplicates():
    text = "Tab Zerodol 100mg BD\nTab Voveran 50mg TDS"
    assert "duplicate" in kinds(text)
    msg = messages(text)
    assert "same" in msg or "both" in msg


def test_hidden_paracetamol_duplication_in_a_combination():
    # Combiflam already contains paracetamol; adding Dolo doubles it.
    text = "T. Dolo 650 1-0-1 AF\nT. Combiflam 1-0-1 SOS"
    flags = check(parse(text))
    assert flags, "a hidden duplication must not pass silently"


# ----------------------------------------------------------- schedule rules
def test_methotrexate_written_daily_is_flagged_as_critical():
    flags = check(parse("Tab Folitrax 15mg OD"))
    schedule = [f for f in flags if f.kind == "schedule"]
    assert schedule, [f.kind for f in flags]
    assert schedule[0].severity == "critical"
    assert "week" in schedule[0].message.lower()


def test_methotrexate_written_weekly_is_not_flagged_for_schedule():
    assert "schedule" not in kinds("Tab Folitrax 10mg once a week Sunday")


def test_worst_severity_ordering():
    flags = check(parse("Tab Folitrax 15mg OD"))
    assert worst_severity(flags) == "critical"


# ------------------------------------------------------- completeness checks
def test_missing_frequency_is_flagged():
    assert "ambiguous" in kinds("Tab Amlong 5mg")


def test_complete_directions_are_not_flagged():
    assert "ambiguous" not in kinds("Tab Amlong 5mg OD x 30 days")


def test_high_risk_drug_gets_a_monitoring_note():
    flags = check(parse("T Warf 5mg 0-0-1"))
    assert any(f.kind in ("high_risk", "monitoring") for f in flags), [f.kind for f in flags]


def test_pregnancy_alert_for_teratogenic_drug():
    assert any(
        "pregnan" in f.message.lower() for f in check(parse("Tab Folitrax 15mg OD"))
    )


# --------------------------------------------------------------- robustness
@pytest.mark.parametrize(
    "text",
    ["", "   ", "Review after 1 week", "asdkjhaskdjh", "T. Unknownbrand 1-0-1"],
)
def test_never_raises_on_odd_input(text):
    check(parse(text))       # must not raise
