"""Regression tests for the verify CLI's strict matcher.

The bug: verify used ``is_same_incident`` (designed for intra-pipeline
merging), which silently skips checks when one side has a null field.
Result: a truth record with only a name matched ANY pipeline case
(because city/date checks short-circuited). Live symptom: a 26-name
January 2026 truth list reported 4 TPs against an Arraba run, but only
1 was real (the other 3 were null-field auto-passes).

Fix: ``_verify_match`` requires AT LEAST ONE positive signal —
strict name match (Jaro ≥ 0.85) OR (city + date) — and never accepts
null-field auto-passes.
"""
from __future__ import annotations

from crime_pipeline.verification import _verify_match, verify_run_against_truth


# ---------------------------------------------------------------------------
# Null-field handling — the main bug fix
# ---------------------------------------------------------------------------

def test_unnamed_case_does_not_match_named_truth() -> None:
    """The bug: a case with no extracted name should NOT match a truth
    record whose name doesn't appear anywhere in the case."""
    truth = {"victim_name_ar": "عدي صقر أبو عمار", "city": None, "incident_date": None}
    case = {"victim_name": None, "victim_name_ar": None, "city": "Arraba",
            "incident_date": "2026-02-03"}
    assert _verify_match(truth, case) is False


def test_completely_different_names_do_not_match() -> None:
    """The actual live false positive: Mayor Ahmed Nassar in pipeline vs
    Abdulrahman Al-Abeera in truth. Different people. MUST NOT match."""
    truth = {"victim_name_ar": "عبدالرحمن عماد العبيرة"}
    case = {"victim_name_ar": "د. أحمد نصّار"}
    assert _verify_match(truth, case) is False


def test_prefix_collision_does_not_match() -> None:
    """The other live false positive: 'إبراهيم خالد غزال' matching
    'خالد غدير' — shared 'خالد' but different people."""
    truth = {"victim_name_ar": "خالد غدير"}
    case = {"victim_name_ar": "إبراهيم خالد غزال"}
    assert _verify_match(truth, case) is False


def test_identical_name_matches() -> None:
    """The one real match from the live January-truth verify."""
    truth = {"victim_name_ar": "بكر محمود ياسين"}
    case = {"victim_name_ar": "بكر محمود ياسين"}
    assert _verify_match(truth, case) is True


def test_one_typo_still_matches() -> None:
    """User flagged 'there may be some typos' — Jaro tolerates 1-2 chars."""
    truth = {"victim_name_ar": "بكر محمود ياسين"}
    case = {"victim_name_ar": "بكر محمد ياسين"}  # محمود → محمد typo
    assert _verify_match(truth, case) is True


def test_substring_name_still_matches() -> None:
    """The Bakr-vs-Bakr-Mahmoud variant still matches because Jaro on
    romanized form (with the inserted middle name) clears 0.85."""
    truth = {"victim_name_ar": "بكر ياسين"}
    case = {"victim_name_ar": "بكر محمود ياسين"}
    # Already verified in earlier reconciler tests: Jaro = 0.914 here
    assert _verify_match(truth, case) is True


# ---------------------------------------------------------------------------
# Empty truth never auto-matches
# ---------------------------------------------------------------------------

def test_truth_record_with_only_null_fields_matches_nothing() -> None:
    """Defensive: a truth record with no usable fields must not match
    anything (formerly it matched everything due to skip-on-null)."""
    truth = {"city": None, "incident_date": None, "victim_name": None}
    case = {"victim_name": "anything", "city": "Arraba", "incident_date": "2026-01-01"}
    assert _verify_match(truth, case) is False


# ---------------------------------------------------------------------------
# City + date alternative path (for anonymous victims)
# ---------------------------------------------------------------------------

def test_city_plus_date_matches_unnamed_case() -> None:
    """A truth record without a name but with city + date can still
    match an unnamed pipeline case in that city on that date."""
    truth = {"victim_name": None, "city": "Arraba", "incident_date": "2026-02-03"}
    case = {"victim_name": None, "city": "Arraba", "incident_date": "2026-02-03"}
    assert _verify_match(truth, case) is True


def test_city_only_does_not_match() -> None:
    """Just city — too weak a signal. MUST NOT match."""
    truth = {"victim_name": None, "city": "Arraba", "incident_date": None}
    case = {"victim_name": None, "city": "Arraba", "incident_date": "2026-02-03"}
    assert _verify_match(truth, case) is False


def test_date_only_does_not_match() -> None:
    """Just date — too weak. MUST NOT match."""
    truth = {"victim_name": None, "city": None, "incident_date": "2026-02-03"}
    case = {"victim_name": None, "city": "Arraba", "incident_date": "2026-02-03"}
    assert _verify_match(truth, case) is False


# ---------------------------------------------------------------------------
# End-to-end with the actual January 2026 false-positive scenarios
# ---------------------------------------------------------------------------

def test_january_2026_verify_only_real_matches() -> None:
    """The live regression: 26-name January truth + 5 Arraba pipeline cases.
    Pre-fix: 4 TPs (3 fuzzy false). Post-fix: 1 TP (Bakr only)."""
    truth = [
        {"victim_name_ar": "عبدالرحمن عماد العبيرة"},
        {"victim_name_ar": "عدي صقر أبو عمار"},
        {"victim_name_ar": "بكر محمود ياسين"},
        {"victim_name_ar": "خالد غدير"},
    ]
    pipeline_cases = [
        {"victim_name_ar": "د. أحمد نصّار"},
        {"victim_name_ar": "مجدي عاطف شلاعطة"},
        {"victim_name_ar": "بكر محمود ياسين"},
        {"victim_name": None, "victim_name_ar": None},
        {"victim_name_ar": "إبراهيم خالد غزال"},
    ]
    result = verify_run_against_truth(truth, pipeline_cases)
    assert result.true_positive == 1
    assert result.false_negative == 3  # the other 3 truth records not matched
    assert result.false_positive == 4  # the other 4 pipeline cases not in truth
