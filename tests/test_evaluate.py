

from __future__ import annotations

import pytest

from rxexplain.evaluate import (
    _ensure_safe_import_path,
    abbreviation_leaks,
    fact_coverage,
    has_disclaimer,
    load_gold,
    readability,
    structure_score,
)


# ------------------------------------------------------------ fact coverage
def test_all_facts_present():
    r = fact_coverage("Take paracetamol 650 mg twice a day.", ["paracetamol", "650"])
    assert r["coverage"] == 1.0
    assert r["missing"] == []


def test_missing_fact_is_reported():
    r = fact_coverage("Take one tablet daily.", ["paracetamol", "650"])
    assert r["coverage"] == 0.0
    assert sorted(r["missing"]) == ["650", "paracetamol"]


def test_alternatives_any_match_counts():
    checks = ["twice a day|two times|morning and night"]
    assert fact_coverage("Take it morning and night.", checks)["coverage"] == 1.0
    assert fact_coverage("Take it twice a day.", checks)["coverage"] == 1.0
    assert fact_coverage("Take it once a day.", checks)["coverage"] == 0.0


def test_matching_is_case_and_whitespace_insensitive():
    assert fact_coverage("PARACETAMOL\n  650   mg", ["paracetamol", "650 mg"])["coverage"] == 1.0


def test_markdown_emphasis_does_not_block_a_match():
    assert fact_coverage("**paracetamol** 650 mg", ["paracetamol"])["coverage"] == 1.0


def test_empty_checklist_yields_none_not_zero():
    # None means "not measurable", which must not be averaged in as a zero.
    assert fact_coverage("anything", [])["coverage"] is None


# --------------------------------------------------------- abbreviation leaks
@pytest.mark.parametrize(
    "text,leaks",
    [
        ("Take one tablet twice a day, morning and night.", 0),
        ("Take 1 tab BD after food.", 1),
        ("Take it OD and the other one TDS.", 2),
        ("Follow the 1-0-1 schedule.", 1),
        ("Continue for 5/7.", 1),
    ],
)
def test_leak_counting(text, leaks):
    assert abbreviation_leaks(text)["leak_count"] == leaks


def test_a_glossed_abbreviation_in_brackets_is_not_a_leak():
    # "twice a day (BD)" is good patient education; a bare "BD" is the failure.
    assert abbreviation_leaks("Take it twice a day (BD), after food.")["clean"] is True


def test_ordinary_prose_is_not_flagged():
    text = (
        "Your doctor wants you to drink plenty of water and to call the clinic "
        "if the pain in your side gets worse or you cannot pass urine."
    )
    assert abbreviation_leaks(text)["leak_count"] == 0


def test_numbers_that_are_not_slot_patterns_are_not_flagged():
    assert abbreviation_leaks("Take it between 8-10 in the morning.")["leak_count"] == 0


# --------------------------------------------------------------- readability
def test_readability_returns_a_plausible_grade():
    simple = " ".join(["Take one tablet at night. It helps you sleep."] * 6)
    hard = " ".join(
        ["Administration of the aforementioned pharmacotherapeutic agent "
         "necessitates consideration of hepatic metabolism."] * 4
    )
    assert readability(simple)["fk_grade"] < readability(hard)["fk_grade"]


def test_short_text_does_not_crash_the_scorer():
    assert readability("Take one tablet.")["fk_grade"] == 0.0


def test_safe_import_path_removes_cwd_before_optional_imports(monkeypatch):
    import sys
    from pathlib import Path

    cwd = str(Path.cwd())
    monkeypatch.setattr(sys, "path", ["", cwd, "/tmp/testpath"])

    _ensure_safe_import_path()

    assert "" not in sys.path
    assert cwd not in sys.path
    assert "/tmp/testpath" in sys.path


def test_headings_are_excluded_from_readability():
    # All-caps headings are not prose and must not distort the grade.
    body = " ".join(["Take one tablet at night with water."] * 8)
    with_headings = "YOUR MEDICINES\n" + body + "\nDISCLAIMER\n"
    assert readability(with_headings)["words"] == readability(body)["words"]


# ------------------------------------------------------- structure/disclaimer
def test_structure_score_counts_headings():
    text = "WHAT THIS PRESCRIPTION IS FOR\nx\nYOUR MEDICINES\ny\nDISCLAIMER\nz"
    r = structure_score(text)
    assert r["headings_present"] == 3
    assert r["structure"] == pytest.approx(3 / 6)


def test_disclaimer_detection():
    assert has_disclaimer("This is not medical advice and does not replace your doctor.")
    assert not has_disclaimer("Take one tablet twice a day.")


# ------------------------------------------------------------------ gold set
def test_gold_set_loads_and_is_well_formed():
    cases = load_gold()
    assert len(cases) >= 40
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        assert c.input_text.strip(), c.case_id
        assert len(c.reference.split()) >= 40, f"{c.case_id}: reference too short"
        assert c.must_include, f"{c.case_id}: no must_include checks"
        assert c.difficulty in ("easy", "medium", "hard"), c.case_id


def test_every_gold_reference_satisfies_its_own_checklist():
    """The human reference must pass the checklist derived from it.

    This is the guard against an unsatisfiable check - a demand no correct answer
    could meet, which would silently penalise every system equally.
    """
    failures: list[str] = []
    for case in load_gold():
        result = fact_coverage(case.reference, case.must_include)
        if result["missing"]:
            failures.append(f"{case.case_id}: {result['missing']}")
    assert not failures, "unsatisfiable checks:\n" + "\n".join(failures)


def test_gold_references_are_free_of_raw_abbreviations():
    bad = [
        f"{c.case_id}: {abbreviation_leaks(c.reference)['codes']}"
        for c in load_gold()
        if not abbreviation_leaks(c.reference)["clean"]
    ]
    assert not bad, "references must model the target style:\n" + "\n".join(bad)
