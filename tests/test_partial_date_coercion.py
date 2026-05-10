"""Regression tests for partial-date coercion.

Background: the LLM emits ``"2026-01-XX"`` style values when an article
gives a partial date ("last month" → year + month known, day unknown).
Pre-fix, Pydantic rejected the entire extraction over the malformed date,
silently losing real homicide records.

Concrete real-world incident this fix targets: the Bakr Yassin homicide
(Arraba al-Buttouf, Jan 2026, doctor accused of killing his brother) was
extracted with all fields correct EXCEPT ``incident_date="2026-01-XX"``.
That single bad field nuked the entire record from the pipeline, dropping
recall on a confirmed homicide.
"""
from __future__ import annotations

import pytest

from crime_pipeline.models import CanonicalCaseSchema, ExtractedArticleData


@pytest.mark.parametrize("bad_date", [
    "2026-01-XX",
    "2026-XX-XX",
    "????-??-??",
    "2026-01-??",
    "XXXX-01-15",
])
def test_extracted_article_coerces_partial_date_to_none(bad_date) -> None:
    """The whole record must survive — partial date becomes None, rest is kept."""
    record = ExtractedArticleData(
        victim_name="Bakr Yassin",
        incident_date=bad_date,
        victim_outcome="died",
    )
    assert record.incident_date is None
    # Other fields preserved
    assert record.victim_name == "Bakr Yassin"
    assert record.victim_outcome == "died"


def test_real_date_passes_through() -> None:
    record = ExtractedArticleData(incident_date="2026-03-20")
    assert record.incident_date is not None
    assert record.incident_date.year == 2026
    assert record.incident_date.month == 3


def test_death_date_also_coerced() -> None:
    record = ExtractedArticleData(death_date="2026-XX-XX")
    assert record.death_date is None


def test_empty_string_becomes_none() -> None:
    record = ExtractedArticleData(incident_date="")
    assert record.incident_date is None


def test_canonical_case_coerces_partial_date_too() -> None:
    """The cleanup stages round-trip through dict — bad legacy dates must
    not crash the re-validation."""
    case = CanonicalCaseSchema(
        victim_name="X",
        incident_date="2026-02-XX",
        incident_date_possible="2026-XX-XX",
    )
    assert case.incident_date is None
    assert case.incident_date_possible is None


def test_bakr_yassin_scenario_full_record_survives() -> None:
    """The exact scenario from the live debug session: every other field
    is good, only the date is partial. Pre-fix: whole record was lost.
    Post-fix: date becomes None but the homicide is preserved."""
    record = ExtractedArticleData(
        victim_name="بكر ياسين",
        victim_name_ar="بكر ياسين",
        victim_gender="M",
        city="عرابة البطوف",
        incident_date="2026-01-XX",  # the killer field
        victim_outcome="died",
        weapon_type="firearm",
        suspect_age=36,
        suspect_relation="brother",
        suspect_status="in_custody",
        legal_status="pre_indictment",
        incident_type="homicide",
    )
    # The whole record exists — that's the key win
    assert record.incident_date is None
    assert record.victim_name == "بكر ياسين"
    assert record.victim_outcome == "died"
    assert record.suspect_relation == "brother"
    assert record.incident_type == "homicide"
