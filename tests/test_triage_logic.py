"""Unit tests for crime_pipeline.extraction.triage — pure logic only.

These exercise the prompt builder, status-derivation, and the constants
that downstream code depends on. The actual LLM call is left to live runs
(testing it offline would mean mocking google.genai which adds zero value).
"""
from __future__ import annotations

from crime_pipeline.extraction.triage import (
    TRIAGE_PROMPT_VERSION,
    TRIAGE_SYSTEM_PROMPT,
    TriageResult,
    _KEEP_TYPES,
    _MAYBE_TYPES,
    _VALID_INCIDENT_TYPES,
    _status_for,
    build_triage_user_prompt,
)


# ---------------------------------------------------------------------------
# Status derivation
# ---------------------------------------------------------------------------

def test_status_for_homicide_is_yes() -> None:
    assert _status_for("homicide") == "yes"


def test_status_for_attempted_homicide_is_yes() -> None:
    assert _status_for("attempted_homicide") == "yes"


def test_status_for_unknown_is_maybe() -> None:
    """Recall-bias: 'unknown' must NEVER be silently dropped."""
    assert _status_for("unknown") == "maybe"


def test_status_for_accident_is_no() -> None:
    assert _status_for("accident") == "no"


def test_status_for_historical_is_no() -> None:
    assert _status_for("historical") == "no"


def test_status_for_other_crime_is_no() -> None:
    assert _status_for("other_crime") == "no"


def test_status_for_non_crime_is_no() -> None:
    assert _status_for("non_crime") == "no"


def test_status_for_suicide_is_no() -> None:
    assert _status_for("suicide") == "no"


# ---------------------------------------------------------------------------
# Categories must stay in lockstep with relevance.py
# ---------------------------------------------------------------------------

def test_categories_match_relevance_filter() -> None:
    """Triage's _KEEP_TYPES must be the same set the relevance filter
    keeps post-extraction. Otherwise we drop in one place and keep in
    the other — silent semantic drift."""
    from crime_pipeline.extraction.relevance import _HOMICIDE_TYPES
    assert _KEEP_TYPES == _HOMICIDE_TYPES


def test_all_valid_incident_types_classified() -> None:
    """Every value in _VALID_INCIDENT_TYPES must produce a status — no
    KeyError surprises if the LLM emits a known category."""
    for t in _VALID_INCIDENT_TYPES:
        assert _status_for(t) in {"yes", "maybe", "no"}


def test_unknown_is_maybe_not_no() -> None:
    """Belt-and-braces: explicit assertion guarding against future drift
    where someone puts 'unknown' in the drop bucket."""
    assert "unknown" in _MAYBE_TYPES
    assert _status_for("unknown") != "no"


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def test_user_prompt_includes_title_and_lede() -> None:
    out = build_triage_user_prompt("Murder in Arraba", "A man was shot dead today.")
    assert "Murder in Arraba" in out
    assert "A man was shot dead today" in out


def test_user_prompt_handles_none_title() -> None:
    out = build_triage_user_prompt(None, "body")
    assert "(no title)" in out


def test_user_prompt_truncates_lede_to_600_chars() -> None:
    long = "x" * 2000
    out = build_triage_user_prompt("title", long)
    # The body section after "FIRST PARAGRAPH:\n" should be at most 600 chars
    body = out.split("FIRST PARAGRAPH:\n", 1)[1]
    assert len(body) <= 600


def test_user_prompt_handles_arabic() -> None:
    out = build_triage_user_prompt("مقتل رجل في عرابة", "قُتل رجل في الأربعين من عمره...")
    assert "مقتل رجل في عرابة" in out
    assert "قُتل" in out


def test_user_prompt_handles_hebrew() -> None:
    out = build_triage_user_prompt("רצח בעראבה", "הערב נרצח אדם בעראבה.")
    assert "רצח בעראבה" in out
    assert "נרצח" in out


# ---------------------------------------------------------------------------
# System prompt invariants
# ---------------------------------------------------------------------------

def test_system_prompt_lists_every_category() -> None:
    """The prompt must mention every category by exact name so the LLM
    doesn't invent variants like 'murder' or 'killing'."""
    for cat in _VALID_INCIDENT_TYPES:
        assert f'"{cat}"' in TRIAGE_SYSTEM_PROMPT, (
            f"category {cat!r} missing from TRIAGE_SYSTEM_PROMPT"
        )


def test_system_prompt_demands_json_only() -> None:
    assert "JSON" in TRIAGE_SYSTEM_PROMPT
    assert "No prose" in TRIAGE_SYSTEM_PROMPT or "no prose" in TRIAGE_SYSTEM_PROMPT.lower()


def test_system_prompt_recall_biased() -> None:
    """The prompt must explicitly tell the LLM to prefer 'unknown' over
    'non_crime' when uncertain — recall is the priority."""
    assert "unknown" in TRIAGE_SYSTEM_PROMPT.lower()
    # The recall-bias direction is encoded in the bullet for "unknown"
    assert "doubt" in TRIAGE_SYSTEM_PROMPT.lower() or "uncertain" in TRIAGE_SYSTEM_PROMPT.lower()


def test_prompt_version_is_set() -> None:
    assert TRIAGE_PROMPT_VERSION
    assert isinstance(TRIAGE_PROMPT_VERSION, str)


# ---------------------------------------------------------------------------
# TriageResult dataclass
# ---------------------------------------------------------------------------

def test_triage_result_has_required_fields() -> None:
    r = TriageResult(
        article_id="abc",
        status="yes",
        incident_type="homicide",
        reason="homicide",
        model_version="gemini-2.5-flash:v1",
        input_tokens=100,
        output_tokens=10,
    )
    assert r.article_id == "abc"
    assert r.status == "yes"
    assert r.incident_type == "homicide"
    assert r.error is None  # default
