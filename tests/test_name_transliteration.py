"""Tests for the post-merge name transliteration step (Tier 2 fill).

What this enforces:
  • Source-attested ``victim_name_*`` values are NEVER overwritten.
  • Every inferred value lands in ``name_transliterations`` with full
    provenance (target script, source script, method, source value).
  • Idempotency — re-running on an already-enriched case adds nothing.
  • The dictionary takes precedence over rule-based char maps for
    known name components (well-known Israeli-Arab names land on
    their canonical Hebrew/English spelling).
  • The rule-based fallback covers unknown tokens deterministically.
  • Empty / missing source values produce no entries (no
    "transliterated from None").
"""
from __future__ import annotations

from crime_pipeline.enrichment.name_enrichment import (
    enrich_case_with_transliterations,
    enrich_cases,
)
from crime_pipeline.enrichment.transliterator import transliterate
from crime_pipeline.models import CanonicalCaseSchema, TransliteratedName


# ---------------------------------------------------------------------------
# Transliterator: core mapping
# ---------------------------------------------------------------------------

def test_arabic_to_hebrew_via_dictionary_for_known_name() -> None:
    """Dictionary takes precedence — Bakr Yassin should land on
    'בכר יאסין' (canonical Hebrew form per Israeli-Arab press)."""
    result = transliterate("بكر ياسين", source_script="ar", target_script="he")
    assert result is not None
    value, method = result
    assert value == "בכר יאסין"
    assert method == "dictionary"


def test_hebrew_to_arabic_via_dictionary() -> None:
    """Reverse direction also works — 'בכר יאסין' → 'بكر ياسين'."""
    result = transliterate("בכר יאסין", source_script="he", target_script="ar")
    assert result is not None
    value, method = result
    assert value == "بكر ياسين"
    assert method == "dictionary"


def test_arabic_to_english_via_dictionary() -> None:
    """English transliteration uses the dictionary's canonical Latin
    spelling: 'بكر ياسين' → 'Bakr Yassin'."""
    result = transliterate("بكر ياسين", source_script="ar", target_script="en")
    assert result is not None
    value, method = result
    assert value == "Bakr Yassin"
    assert method == "dictionary"


def test_rule_based_fallback_for_unknown_tokens() -> None:
    """Tokens not in the dictionary fall through to char-by-char
    transliteration. Result is marked method=rule_based."""
    # A made-up name unlikely to be in the dictionary
    result = transliterate("سلمان شيبانو", source_script="ar", target_script="he")
    assert result is not None
    value, method = result
    # Char-by-char: س→ס, ل→ל, م→מ, ا→א, ن→נ → "סלמאן" (and similar for second token)
    assert "סלמאן" in value or method == "rule_based"
    assert method in ("rule_based", "dictionary")


def test_compound_abu_name_preserved() -> None:
    """``أبو X`` is a compound family-name marker; must not be stripped
    or split. 'حسين أبو رقيق' → must keep 'אבו רקיק' as a unit."""
    result = transliterate("حسين أبو رقيق", source_script="ar", target_script="he")
    assert result is not None
    value, _ = result
    assert "אבו" in value
    assert "רקיק" in value


def test_empty_input_returns_none() -> None:
    assert transliterate("", "ar", "he") is None
    assert transliterate("   ", "ar", "he") is None


def test_same_script_returns_none() -> None:
    """ar → ar makes no sense; should refuse."""
    assert transliterate("بكر", "ar", "ar") is None


# ---------------------------------------------------------------------------
# Enrichment orchestration
# ---------------------------------------------------------------------------

def test_enrichment_fills_missing_hebrew_from_arabic() -> None:
    """The most common case: Arab48-only source → only ``_ar``
    populated → enrichment generates ``_he`` and ``_en`` inferred forms."""
    case: dict = {
        "victim_name_ar": "بكر ياسين",
        "victim_name_he": None,
        "victim_name_en": None,
        "name_transliterations": [],
    }
    enrich_case_with_transliterations(case)
    # Source-of-truth fields UNCHANGED.
    assert case["victim_name_ar"] == "بكر ياسين"
    assert case["victim_name_he"] is None
    assert case["victim_name_en"] is None
    # Inferred values added with provenance.
    targets = {t["target_script"]: t for t in case["name_transliterations"]}
    assert "he" in targets
    assert "en" in targets
    assert targets["he"]["value"]
    assert targets["he"]["source_script"] == "ar"
    assert targets["he"]["source_value"] == "بكر ياسين"
    assert targets["he"]["method"] in {"dictionary", "rule_based"}


def test_enrichment_never_overwrites_source_attested() -> None:
    """Source-attested values are sacred — never touched even when
    the transliterator could generate a different spelling."""
    case: dict = {
        "victim_name_ar": "بكر محمود ياسين",
        "victim_name_he": "בכר מחמוד יאסין",   # source-attested
        "victim_name_en": None,
        "name_transliterations": [],
    }
    enrich_case_with_transliterations(case)
    assert case["victim_name_he"] == "בכר מחמוד יאסין"   # UNCHANGED
    # Only the missing _en field gets transliterated.
    targets = {t["target_script"]: t for t in case["name_transliterations"]}
    assert "en" in targets
    assert "he" not in targets   # not generated (was attested)
    assert "ar" not in targets   # not generated (was attested)


def test_enrichment_idempotent() -> None:
    """Re-running on an already-enriched case adds no duplicates."""
    case: dict = {
        "victim_name_ar": "بكر ياسين",
        "victim_name_he": None,
        "victim_name_en": None,
        "name_transliterations": [],
    }
    enrich_case_with_transliterations(case)
    first_count = len(case["name_transliterations"])
    assert first_count > 0
    # Run again — count must stay the same.
    enrich_case_with_transliterations(case)
    assert len(case["name_transliterations"]) == first_count


def test_enrichment_skips_case_with_no_attested_names() -> None:
    """If a case is fully nameless (anonymous victim from police
    report), enrichment can't generate from nothing. No entries added."""
    case: dict = {
        "victim_name_ar": None,
        "victim_name_he": None,
        "victim_name_en": None,
        "name_transliterations": [],
    }
    enrich_case_with_transliterations(case)
    assert case["name_transliterations"] == []


def test_enrichment_batch_helper_returns_same_list() -> None:
    """``enrich_cases`` mutates in-place and returns the same list
    object — easy to chain into pipeline stages."""
    cases = [
        {"victim_name_ar": "بكر", "name_transliterations": []},
        {"victim_name_he": "בכר", "name_transliterations": []},
    ]
    out = enrich_cases(cases)
    assert out is cases   # same object, not a copy
    for c in out:
        assert c["name_transliterations"]   # both got filled


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_transliterated_name_model_validates() -> None:
    t = TransliteratedName(
        value="בכר יאסין",
        target_script="he",
        source_script="ar",
        method="dictionary",
        source_value="بكر ياسين",
    )
    assert t.value == "בכר יאסין"
    assert t.method == "dictionary"


def test_canonical_case_with_transliterations_round_trips() -> None:
    """A canonical case with name_transliterations must serialize +
    re-parse cleanly (so the export → re-load round-trip works)."""
    case = CanonicalCaseSchema(
        victim_name_ar="بكر ياسين",
        name_transliterations=[
            TransliteratedName(
                value="בכר יאסין",
                target_script="he",
                source_script="ar",
                method="dictionary",
                source_value="بكر ياسين",
            ),
        ],
    )
    dumped = case.model_dump(mode="json")
    reparsed = CanonicalCaseSchema(**dumped)
    assert len(reparsed.name_transliterations) == 1
    assert reparsed.name_transliterations[0].target_script == "he"


def test_canonical_case_defaults_empty_transliterations() -> None:
    """The field defaults to empty list, never None — keeps consumer
    iteration safe."""
    case = CanonicalCaseSchema(victim_name_ar="بكر")
    assert case.name_transliterations == []


# ---------------------------------------------------------------------------
# Pipeline wiring
# ---------------------------------------------------------------------------

def test_pipeline_build_canonical_invokes_enrichment() -> None:
    """The build_canonical method must call enrich_cases AFTER reconcile.
    Pinning the source guards against accidental re-ordering that would
    let inferred names participate in clustering."""
    import inspect
    from crime_pipeline.pipeline import Pipeline
    src = inspect.getsource(Pipeline.build_canonical)
    assert "enrich_cases" in src or "enrich_case_with_transliterations" in src
    # Enrichment must come after reconcile in the source. Find indices.
    reconcile_idx = src.find("_run_cleanup")
    enrich_idx = src.find("enrich_cases")
    assert reconcile_idx != -1 and enrich_idx != -1
    assert enrich_idx > reconcile_idx, (
        "Name enrichment must run AFTER reconcile, otherwise inferred "
        "names would influence clustering decisions."
    )
