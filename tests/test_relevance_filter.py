"""Tests for the post-extract relevance gate.

The gate sits between the extract and dedup stages and drops extractions
that show no signal of being a real homicide. It's deliberately
conservative: false-negatives (dropping a real case) are worse than
false-positives (junk in the output that an operator can flag).
"""
from __future__ import annotations

import types

from crime_pipeline.extraction.relevance import (
    _is_present,
    is_homicide_extraction,
)


# ---------------------------------------------------------------------------
# _is_present helper
# ---------------------------------------------------------------------------

def test_is_present_returns_false_for_none() -> None:
    assert _is_present(None) is False


def test_is_present_treats_placeholder_strings_as_empty() -> None:
    assert _is_present("") is False
    assert _is_present("unknown") is False
    assert _is_present("Unknown") is False  # case-insensitive
    assert _is_present("  N/A  ") is False  # whitespace stripped
    assert _is_present("לא ידוע") is False  # Hebrew "unknown"
    assert _is_present("غير معروف") is False  # Arabic "unknown"


def test_is_present_returns_true_for_real_strings() -> None:
    assert _is_present("Bakr Yassin") is True
    assert _is_present("Arraba") is True
    assert _is_present("2026-01-04") is True


def test_is_present_handles_lists() -> None:
    assert _is_present([]) is False
    assert _is_present([None, None]) is False
    assert _is_present(["unknown", ""]) is False
    assert _is_present(["Bakr"]) is True
    assert _is_present([None, "Yassin"]) is True


# ---------------------------------------------------------------------------
# is_homicide_extraction — drop rules
# ---------------------------------------------------------------------------

def test_drops_none_input() -> None:
    """Defensive: parse-failed extractions or stale rows."""
    keep, reason = is_homicide_extraction(None)
    assert keep is False
    assert reason == "no_extraction_data"


def test_drops_extraction_with_no_signal() -> None:
    """The exact case our Arraba 2026 run produced: 3 fully-empty cases."""
    keep, reason = is_homicide_extraction({
        "victim_name": None,
        "victim_name_ar": None,
        "victim_name_he": None,
        "victim_name_en": None,
        "victim_aliases": [],
        "city": None,
        "incident_date": None,
        "death_date": None,
        "victim_outcome": None,
    })
    assert keep is False
    assert reason == "no_homicide_signal"


def test_drops_extraction_with_only_placeholder_strings() -> None:
    """The LLM occasionally emits 'unknown' instead of null."""
    keep, reason = is_homicide_extraction({
        "victim_name": "unknown",
        "city": "לא ידוע",
        "incident_date": None,
        "victim_outcome": None,
    })
    assert keep is False
    assert reason == "no_homicide_signal"


def test_drops_survived_victim_even_with_full_data() -> None:
    """Non-fatal incidents are not homicides; export drops them anyway."""
    keep, reason = is_homicide_extraction({
        "victim_name": "Ahmed Nasser",
        "city": "Arraba",
        "incident_date": "2026-01-04",
        "victim_outcome": "survived",
    })
    assert keep is False
    assert reason == "victim_survived"


# ---------------------------------------------------------------------------
# incident_type discriminator — drop non-homicide categories
# ---------------------------------------------------------------------------

def test_drops_accident_even_with_full_data() -> None:
    """The Arraba tractor case: outcome=died, city, date, but NOT a homicide."""
    keep, reason = is_homicide_extraction({
        "incident_type": "accident",
        "victim_name": "Ibrahim Khaled Ghazal",
        "city": "Arraba",
        "incident_date": "2026-03-10",
        "victim_outcome": "died",
        "weapon_type": "vehicle",
    })
    assert keep is False
    assert reason == "incident_type:accident"


def test_drops_historical_retrospective() -> None:
    """The Land Day case: LLM extracted outcome=died with num_victims=6 from
    a 1976 anniversary article. Not a current incident."""
    keep, reason = is_homicide_extraction({
        "incident_type": "historical",
        "victim_name": None,
        "city": "Arraba",
        "incident_date": "2026-03-30",
        "victim_outcome": "died",
        "num_victims": 6,
    })
    assert keep is False
    assert reason == "incident_type:historical"


def test_drops_other_crime_arrest() -> None:
    """The Tel Aviv cheating-arrest case: suspect_status=arrested, no victim."""
    keep, reason = is_homicide_extraction({
        "incident_type": "other_crime",
        "city": "Tel Aviv",
        "suspect_status": "arrested",
    })
    assert keep is False
    assert reason == "incident_type:other_crime"


def test_drops_suicide() -> None:
    keep, reason = is_homicide_extraction({
        "incident_type": "suicide",
        "victim_name": "X",
        "city": "Y",
        "victim_outcome": "died",
    })
    assert keep is False
    assert reason == "incident_type:suicide"


def test_drops_non_crime() -> None:
    """Protest article with 'crime victims' rhetoric — non-crime."""
    keep, reason = is_homicide_extraction({
        "incident_type": "non_crime",
        "city": "Arraba",
    })
    assert keep is False
    assert reason == "incident_type:non_crime"


def test_keeps_attempted_homicide() -> None:
    """Mayor-shooting case: shot, in critical condition. Keep so reconcile
    can promote outcome later if a follow-up confirms death."""
    keep, reason = is_homicide_extraction({
        "incident_type": "attempted_homicide",
        "victim_name": "Ahmed Nasser",
        "city": "Arraba",
        "incident_date": "2026-03-09",
        "victim_outcome": "critical",
    })
    assert keep is True
    assert reason == "kept"


def test_keeps_homicide() -> None:
    """The Magdi Atef case after the thinking-budget fix."""
    keep, reason = is_homicide_extraction({
        "incident_type": "homicide",
        "victim_name": "مجدي عاطف شلاعطة",
        "city": "عرابة",
        "incident_date": "2026-03-20",
        "victim_outcome": "died",
        "weapon_type": "firearm",
    })
    assert keep is True
    assert reason == "kept"


def test_unknown_type_falls_through_to_field_check() -> None:
    """incident_type='unknown' should not auto-drop — fall back to legacy
    signal-presence check so we don't lose ambiguous-but-real cases."""
    keep, reason = is_homicide_extraction({
        "incident_type": "unknown",
        "victim_name": "Some Name",
        "city": "Arraba",
    })
    assert keep is True


def test_legacy_extraction_without_incident_type() -> None:
    """Backward compat: pre-discriminator extractions (incident_type=None)
    use the legacy signal-presence rules."""
    keep, reason = is_homicide_extraction({
        # No incident_type field at all (legacy DB row)
        "victim_name": "Bakr Yassin",
        "city": "Arraba",
        "victim_outcome": "died",
    })
    assert keep is True


def test_survived_overrides_homicide_type() -> None:
    """Even if incident_type='homicide', if outcome=survived, drop it.
    The article likely mis-categorised; the export filter would drop it
    anyway, but doing it here saves dedup/merge work."""
    keep, reason = is_homicide_extraction({
        "incident_type": "homicide",
        "victim_name": "X",
        "victim_outcome": "survived",
    })
    assert keep is False
    assert reason == "victim_survived"


# ---------------------------------------------------------------------------
# is_homicide_extraction — keep rules (legacy fallback)
# ---------------------------------------------------------------------------

def test_keeps_named_victim_only() -> None:
    """A named victim alone is enough — date/city may come from another source."""
    keep, reason = is_homicide_extraction({
        "victim_name": "Bakr Yassin",
        "city": None,
        "incident_date": None,
        "victim_outcome": None,
    })
    assert keep is True
    assert reason == "kept"


def test_keeps_arabic_only_named_victim() -> None:
    keep, reason = is_homicide_extraction({
        "victim_name": None,
        "victim_name_ar": "بكر ياسين",
        "city": None,
        "incident_date": None,
        "victim_outcome": None,
    })
    assert keep is True


def test_keeps_city_only_breaking_news() -> None:
    """A breaking-news article may have only a city; we let merge/reconcile fill the rest."""
    keep, reason = is_homicide_extraction({
        "victim_name": None,
        "city": "Arraba",
        "incident_date": None,
        "victim_outcome": None,
    })
    assert keep is True


def test_keeps_critical_outcome_pending_followup() -> None:
    """Critical victims may die later — let downstream stages promote outcome."""
    keep, reason = is_homicide_extraction({
        "victim_name": None,
        "city": None,
        "incident_date": None,
        "victim_outcome": "critical",
    })
    assert keep is True


def test_keeps_died_outcome_no_other_fields() -> None:
    """Confirmed death is enough signal even without name/city/date."""
    keep, reason = is_homicide_extraction({
        "victim_outcome": "died",
    })
    assert keep is True


def test_keeps_full_homicide_record() -> None:
    keep, reason = is_homicide_extraction({
        "victim_name": "Bakr Yassin",
        "victim_name_ar": "بكر ياسين",
        "city": "Arraba",
        "incident_date": "2026-01-04",
        "victim_outcome": "died",
    })
    assert keep is True
    assert reason == "kept"


def test_keeps_aliases_only() -> None:
    """Aliases count as victim identity."""
    keep, reason = is_homicide_extraction({
        "victim_aliases": ["Bakr"],
        "city": None,
        "incident_date": None,
    })
    assert keep is True


# ---------------------------------------------------------------------------
# Pipeline integration — _filter_relevance
# ---------------------------------------------------------------------------

def _stub_extraction_record(extracted_json: dict, article_id: str = "abc12345") -> object:
    """Mimic the ExtractedRecord ORM row interface the filter expects."""
    return types.SimpleNamespace(
        id=f"ext-{article_id}",
        article_id=article_id,
        extracted_json=extracted_json,
    )


def _stub_pipeline():
    """Minimal pipeline stand-in with what _filter_relevance touches."""
    from crime_pipeline.pipeline import Pipeline
    return types.SimpleNamespace(
        stats={
            "relevance_kept": 0,
            "relevance_dropped": 0,
            "relevance_drop_reasons": {},
        },
        _filter_relevance=Pipeline._filter_relevance,  # bind via __get__
    )


def test_pipeline_filter_drops_empty_keeps_real() -> None:
    pipe = _stub_pipeline()
    extractions = [
        _stub_extraction_record({"victim_name": None, "city": None, "incident_date": None}, "junk001"),
        _stub_extraction_record({"victim_name": "Bakr", "city": "Arraba"}, "real001"),
        _stub_extraction_record({"victim_name": "Ahmed", "victim_outcome": "survived"}, "surv001"),
        _stub_extraction_record({"victim_outcome": "died"}, "died001"),
    ]
    kept = pipe._filter_relevance(pipe, extractions)
    assert len(kept) == 2
    kept_ids = {e.article_id for e in kept}
    assert kept_ids == {"real001", "died001"}
    assert pipe.stats["relevance_kept"] == 2
    assert pipe.stats["relevance_dropped"] == 2
    reasons = pipe.stats["relevance_drop_reasons"]
    assert reasons.get("no_homicide_signal") == 1
    assert reasons.get("victim_survived") == 1


def test_pipeline_filter_handles_empty_input() -> None:
    """Empty list in → empty list out, stats reflect zero work."""
    pipe = _stub_pipeline()
    kept = pipe._filter_relevance(pipe, [])
    assert kept == []
    assert pipe.stats["relevance_kept"] == 0
    assert pipe.stats["relevance_dropped"] == 0
    assert pipe.stats["relevance_drop_reasons"] == {}


def test_pipeline_filter_handles_missing_extracted_json() -> None:
    """Edge: an extraction row with extracted_json=None should drop, not crash."""
    pipe = _stub_pipeline()
    bad = types.SimpleNamespace(id="ext-bad", article_id="bad00001", extracted_json=None)
    kept = pipe._filter_relevance(pipe, [bad])
    assert kept == []
    assert pipe.stats["relevance_dropped"] == 1
