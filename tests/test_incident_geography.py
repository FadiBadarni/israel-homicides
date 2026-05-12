"""Tests for the ``incident_geography`` field that replaces the
historical name+city blocklists in build_validated_2026.py.

Architectural shift: instead of post-hoc filtering known-foreign cases
by hardcoded name/city substrings (a treadmill that grew with every
new article), the LLM is now asked to classify each article's
geography directly at extraction time. The relevance filter drops
everything except ``israel_arab_society`` (and optionally ``unknown``
for human review).

These tests pin:
  • The Literal enum on both ``ExtractedArticleData`` and
    ``CanonicalCaseSchema`` (drift = silent breakage).
  • The prompt's GEOGRAPHY rule block + JSON schema entry.
  • The merger's value-based priority resolution
    (israel_arab_society > unknown when sources disagree).
"""
from __future__ import annotations

import inspect

from crime_pipeline.extraction.prompts import SYSTEM_PROMPT
from crime_pipeline.models import (
    CanonicalCaseSchema,
    ExtractedArticleData,
)


_EXPECTED_VALUES = {
    "israel_arab_society",
    "israel_jewish_society",
    "israel_other",
    "palestinian_territories",
    "abroad",
    "unknown",
}


# ---------------------------------------------------------------------------
# Schema fields
# ---------------------------------------------------------------------------

def test_extracted_article_data_has_incident_geography_field() -> None:
    """``ExtractedArticleData.incident_geography`` must exist with the
    canonical 6-value Literal."""
    assert "incident_geography" in ExtractedArticleData.model_fields


def test_extracted_article_data_geography_defaults_to_none() -> None:
    """Backward compat: legacy extractions made before the field
    existed should round-trip cleanly with None."""
    obj = ExtractedArticleData()
    assert obj.incident_geography is None


def test_extracted_article_data_accepts_each_geography_value() -> None:
    """All six categories must validate. Any rename or removal here
    is a breaking schema change for downstream UIs."""
    for v in _EXPECTED_VALUES:
        obj = ExtractedArticleData(incident_geography=v)
        assert obj.incident_geography == v


def test_extracted_article_data_rejects_unknown_geography_value() -> None:
    """Defensive: typos or stray values must fail validation."""
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        ExtractedArticleData(incident_geography="usa")  # type: ignore[arg-type]


def test_canonical_case_schema_has_incident_geography_field() -> None:
    assert "incident_geography" in CanonicalCaseSchema.model_fields


def test_canonical_case_accepts_geography() -> None:
    case = CanonicalCaseSchema(incident_geography="israel_arab_society")
    assert case.incident_geography == "israel_arab_society"


# ---------------------------------------------------------------------------
# Prompt + JSON schema
# ---------------------------------------------------------------------------

def test_system_prompt_documents_geography_rule() -> None:
    """The prompt must explain the categories so the LLM has a chance
    of classifying correctly. Pin the section heading and each value."""
    assert "INCIDENT GEOGRAPHY" in SYSTEM_PROMPT
    for v in _EXPECTED_VALUES:
        assert v in SYSTEM_PROMPT, f"missing geography value: {v}"


def test_system_prompt_geography_in_json_schema_block() -> None:
    """The JSON schema block in the prompt must include the
    ``incident_geography`` key — without it the LLM may emit valid
    JSON that lacks the new field."""
    # Find the JSON schema block (starts after "JSON Schema you must follow:")
    idx = SYSTEM_PROMPT.find("JSON Schema you must follow:")
    assert idx >= 0
    schema_block = SYSTEM_PROMPT[idx:]
    assert '"incident_geography"' in schema_block


def test_geography_rule_warns_about_common_foreign_cases() -> None:
    """The prompt should mention Gaddafi-class examples so the model
    doesn't classify high-profile foreign cases as Israeli."""
    geo_section = SYSTEM_PROMPT[
        SYSTEM_PROMPT.find("INCIDENT GEOGRAPHY"):
        SYSTEM_PROMPT.find("INCIDENT TYPE")
    ]
    # At least one mention of a foreign-country indicator must be present.
    foreign_indicators = ["Tehran", "Gaza", "Iran", "Libya", "Gaddafi", "Minneapolis"]
    assert any(ind in geo_section for ind in foreign_indicators), (
        "Geography section must include at least one foreign-indicator "
        "example to ground the model's classification."
    )


# ---------------------------------------------------------------------------
# Merger priority resolution
# ---------------------------------------------------------------------------

def test_merger_resolves_geography_via_value_priority() -> None:
    """When sources disagree on geography, the merger must prefer the
    more specific value (israel_arab_society > unknown). The actual
    priority logic is inline in merger.py; this test inspects the
    source to pin the priority ordering literal."""
    from crime_pipeline.merging import merger as merger_module
    src = inspect.getsource(merger_module)
    assert "incident_geography" in src
    assert '"israel_arab_society": 0' in src
    assert '"unknown": 5' in src


# ---------------------------------------------------------------------------
# Build script — ensure the blocklist is GONE
# ---------------------------------------------------------------------------

def test_build_validated_script_has_no_name_blocklist() -> None:
    """The hardcoded ``_NON_ARAB_SOCIETY_NAMES`` /
    ``_NON_ARAB_SOCIETY_NAME_HINTS`` / ``_NON_ISRAEL_CITY_HINTS``
    constants were a maintenance treadmill. They must not return.
    The declarative ``incident_geography`` filter replaces them.

    Checks for ASSIGNMENT (constant definition), not bare mention,
    so a doc paragraph explaining the historical removal still passes.
    """
    from pathlib import Path
    src = Path("scripts/build_validated_2026.py").read_text(encoding="utf-8")
    forbidden_definitions = [
        "_NON_ARAB_SOCIETY_NAMES = ",
        "_NON_ARAB_SOCIETY_NAMES={",
        "_NON_ARAB_SOCIETY_NAME_HINTS = ",
        "_NON_ARAB_SOCIETY_NAME_HINTS=[",
        "_NON_ISRAEL_CITY_HINTS = ",
        "_NON_ISRAEL_CITY_HINTS=[",
    ]
    for pat in forbidden_definitions:
        assert pat not in src, (
            f"Blocklist constant resurfaced: {pat!r}. "
            "Use incident_geography filter instead."
        )


def test_build_validated_script_uses_geography_filter() -> None:
    """Confirm the script actively filters on incident_geography."""
    from pathlib import Path
    src = Path("scripts/build_validated_2026.py").read_text(encoding="utf-8")
    assert "_geography_passes" in src
    assert "_ALLOWED_GEOGRAPHIES" in src
    # The allowed set must include the target + uncertainty bucket.
    assert "israel_arab_society" in src
    assert "unknown" in src
