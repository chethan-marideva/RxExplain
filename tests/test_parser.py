

from __future__ import annotations

import pytest

from rxexplain.parser_rules import parse, parse_line


def one(line: str):
    med = parse_line(line)
    assert med is not None, f"failed to parse: {line!r}"
    return med


# ------------------------------------------------------------- slot notation
@pytest.mark.parametrize(
    "line,times,units,human_bits",
    [
        ("T. Dolo 650 1-0-1 x 5 days AF", 2.0, 2.0, ["morning", "night"]),
        ("Tab Augmentin 625mg 1-1-1 x 7 days", 3.0, 3.0, ["morning", "afternoon", "night"]),
        ("T. Shelcal 500 1-0-0", 1.0, 1.0, ["morning"]),
        ("Tab Ecosprin 75 0-0-1", 1.0, 1.0, ["night"]),
        ("Tab Eltroxin 1/2-0-1/2", 2.0, 1.0, ["half"]),
    ],
)
def test_slot_patterns(line, times, units, human_bits):
    med = one(line)
    assert med.times_per_day == times
    assert med.units_per_day == pytest.approx(units)
    for bit in human_bits:
        assert bit in med.frequency_human.lower()


# --------------------------------------------------------------- frequencies
@pytest.mark.parametrize(
    "code,times",
    [("OD", 1.0), ("BD", 2.0), ("TDS", 3.0), ("QID", 4.0), ("HS", 1.0), ("OM", 1.0)],
)
def test_frequency_codes(code, times):
    med = one(f"Tab Testdrug 100mg {code}")
    assert med.times_per_day == times
    assert med.frequency_code == code


def test_written_code_is_preserved_not_the_dict_key():
    # "T.D.S" and "TDS" share a normalised key; the patient must see what the
    # prescriber wrote, not our internal key.
    assert one("Tab Metrogyl 400mg TDS").frequency_code == "TDS"


def test_once_a_week_is_parsed():
    # The flagship safety case: methotrexate weekly vs daily.
    med = one("Tab Folitrax 15mg once a week")
    assert med.times_per_day == pytest.approx(1 / 7)
    assert "week" in med.frequency_human.lower()


def test_every_n_hours():
    med = one("Tab Dolo 650 q6h")
    assert med.times_per_day == 4.0


# ------------------------------------------------------------------ duration
@pytest.mark.parametrize(
    "line,expected",
    [
        ("T. Dolo 650 BD x 5/7", "5 days"),
        ("T. Dolo 650 BD x 2/52", "2 weeks"),
        ("T. Dolo 650 BD x 3/12", "3 months"),
        ("T. Dolo 650 BD x 7 days", "7 days"),
        ("T. Dolo 650 BD for 10 days", "10 days"),
    ],
)
def test_durations(line, expected):
    assert one(line).duration == expected


def test_missing_duration_stays_none():
    assert one("Tab Amlong 5mg OD").duration is None


# -------------------------------------------------------------------- timing
@pytest.mark.parametrize(
    "line,timing",
    [
        ("T. Dolo 650 BD AF", "after food"),
        ("Cap Omez 20mg OD BF", "before food"),
        ("T. Eltroxin 75mcg OD empty stomach", "on an empty stomach"),
        ("T. Pan 40 OD before breakfast", "before breakfast"),
        ("Tab Glycomet 500mg BD with meals", "with food"),
        ("T. Razo D 1-0-0 BBF", "before breakfast"),
    ],
)
def test_timings(line, timing):
    assert one(line).timing == timing


def test_multiword_timing_does_not_leak_into_the_name():
    for line in (
        "Tab Augmentin 625mg TDS after food",
        "Tab Eltroxin 75mcg OD empty stomach",
        "Tab Telma 40 OD morning",
    ):
        name = one(line).name or ""
        for bad in ("after", "food", "empty", "stomach", "morning"):
            assert bad not in name.lower(), f"{bad!r} leaked into {name!r}"


# ------------------------------------------------------------- strength/dose
def test_strength_and_unit():
    med = one("Cap Omez 20mg OD")
    assert med.strength == "20 mg"
    assert med.strength_value == 20.0
    assert med.strength_unit == "mg"


def test_brand_trailing_number_infers_mg():
    med = one("T. Dolo 650 1-0-1")
    assert med.strength_value == 650.0
    assert med.generic and "paracetamol" in med.generic.lower()


def test_bare_dose_count_is_not_part_of_the_name():
    med = one("Cap Becosules 1 OD")
    assert med.name == "Becosules"
    assert med.dose_amount == "1 capsule"


def test_liquid_volume_is_a_dose_not_a_strength():
    med = one("Syp Looz 15ml HS")
    assert med.dose_amount == "15 ml"
    assert med.strength is None


def test_concentration_denominator_is_discarded():
    med = one("Syp Zifi 50mg/5ml 5ml BD x 5 days")
    assert med.strength == "50 mg"
    assert med.dose_amount == "5 ml"


def test_units_for_insulin():
    med = one("Inj. Mixtard 30/70 12 units SC before breakfast")
    assert med.dose_amount == "12 units"
    assert med.route is not None and "under the skin" in med.route


# ------------------------------------------------------------ prn and stat
def test_prn():
    assert one("T. Dolo 650 SOS").prn is True
    assert one("T. Dolo 650 BD").prn is False


def test_qid_sos_keeps_both():
    med = one("Syp. Crocin 5ml QID SOS fever")
    assert med.times_per_day == 4.0
    assert med.prn is True
    assert med.name == "Crocin"
    assert any("fever" in n for n in med.notes)


def test_day_of_week():
    med = one("Tab Folitrax 10mg once a week Sunday")
    assert any("sunday" in n.lower() for n in med.notes)


# ------------------------------------------------------- names and coverage
@pytest.mark.parametrize(
    "line,generic_bit",
    [
        ("T. Dolo 650 1-0-1", "paracetamol"),
        ("Tb. Nurokind Plus 1 OD", "vitamin b"),
        ("T. Shelcal-CT 1-0-1", "calcium"),
        ("Tab Metolar XL 25 OD", "metoprolol"),
        ("T. Norflox-TZ BD", "norfloxacin"),
        ("Cap Razo D 1-0-0", "rabeprazole"),
    ],
)
def test_brand_resolution(line, generic_bit):
    med = one(line)
    assert med.generic and generic_bit in med.generic.lower()


def test_out_of_vocabulary_drug_still_parses_the_schedule():
    med = one("T. Martifur MR 100mg BD x 5 days AF")
    assert med.generic is None          # deliberately not in the KB
    assert med.times_per_day == 2.0
    assert med.duration == "5 days"
    assert med.strength == "100 mg"


def test_multiline_coverage_and_instructions():
    text = (
        "1) Tab Augmentin 625mg 1-1-1 x 7 days after food\n"
        "2) Tab Pan 40 1-0-0 before breakfast x 7 days\n"
        "3) Tab Zerodol-SP 1-0-1 SOS for pain\n"
        "4) Plenty of oral fluids and steam inhalation\n"
        "5) Review after 1 week"
    )
    parsed = parse(text)
    assert len(parsed.medications) == 3
    assert parsed.coverage == 1.0
    assert parsed.general_instructions, "advice lines should be kept, not dropped"


def test_non_prescription_text_yields_no_medications():
    parsed = parse("Review after 1 week\nPlenty of oral fluids")
    assert parsed.medications == []


# --------------------------------------------------- slash-separated records
def test_slash_with_spaces_separates_two_drugs():
    """"T Warf 5mg 0-0-1 / Tab Brufen 400mg TDS" is two drugs, not one.

    Regression: the slash was previously left inside a single line, which merged
    both drugs into one medication and applied the second drug's strength to the
    first - so the safety layer never saw the warfarin + NSAID pair at all.
    """
    p = parse("T Warf 5mg 0-0-1 / Tab Brufen 400mg TDS x 5 days")
    assert len(p.medications) == 2
    assert "warf" in p.medications[0].name.lower()
    assert "brufen" in p.medications[1].name.lower()
    # the merge bug leaked the next drug's name into the first one
    assert "brufen" not in p.medications[0].name.lower()


def test_three_drugs_on_one_slash_separated_line():
    p = parse("T. Dolo 650 1-0-1 / Cap Omez 20mg OD BF / Tab Zerodol SP 1-0-1")
    assert len(p.medications) == 3


def test_bare_slash_is_not_a_separator():
    """Durations, fractions and concentrations all contain a bare slash."""
    p = parse("Tab Amoxil 500mg TDS x 5/7")
    assert len(p.medications) == 1
    assert "5 days" in (p.medications[0].duration or "")

    p = parse("Tab Eltroxin 1/2-0-1/2")
    assert len(p.medications) == 1
    assert p.medications[0].times_per_day == 2.0
    assert p.medications[0].units_per_day == pytest.approx(1.0)

    p = parse("Syp Crocin 250mg/5ml 5ml TDS x 3 days")
    assert len(p.medications) == 1
    assert "crocin" in p.medications[0].name.lower()


def test_slash_separated_drugs_reach_the_safety_layer():
    from rxexplain.safety import check

    p = parse("T Warf 5mg 0-0-1 / Tab Brufen 400mg TDS x 5 days")
    kinds = {f.kind for f in check(p)}
    assert "interaction" in kinds
