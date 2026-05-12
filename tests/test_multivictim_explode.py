"""Tests for the multi-victim explode step.

The pipeline used to assume 1 article = 1 victim. When a summary article
("13 قتيلا منذ بدء العام") or a multi-target shooting article ("ياسر
وكامل حجيرات وخالد غدير قتلوا في عرابة") lists multiple named victims,
extraction would pick one and drop the rest — silently dropping every
non-headlining victim from the dataset.

The fix splits this into two layers:
  1. `additional_victims: list[AdditionalVictim]` on ExtractedArticleData
     (LLM populates it when multiple victims are described).
  2. `explode_extraction()` flattens the extraction into N+1 per-victim
     virtual records (primary at index 0; each additional victim at
     index ≥ 1, with per-victim fields swapped in).

These tests cover the explode step in isolation — no DB, no LLM.
"""
from __future__ import annotations

from crime_pipeline.extraction.multivictim import (
    explode_extraction,
    victim_count,
)
from crime_pipeline.models import AdditionalVictim, ExtractedArticleData


# ---------------------------------------------------------------------------
# Single-victim — current behavior preserved
# ---------------------------------------------------------------------------

def test_explode_single_victim_emits_one_record() -> None:
    """Articles with empty additional_victims must explode to length 1
    (the primary). This preserves all existing single-victim flow."""
    ext = {
        "victim_name_ar": "بكر ياسين",
        "city": "Arraba",
        "incident_date": "2026-01-03",
        "incident_type": "homicide",
    }
    out = explode_extraction(ext)
    assert len(out) == 1
    assert out[0]["victim_index"] == 0
    assert out[0]["victim_name_ar"] == "بكر ياسين"
    # additional_victims must be stripped from virtual records to prevent
    # accidental double-explode downstream.
    assert "additional_victims" not in out[0]


def test_explode_missing_additional_victims_key() -> None:
    """If extracted_json doesn't even have the key (legacy extraction),
    explode treats it as empty list — no crash."""
    ext = {"victim_name_ar": "بكر ياسين", "city": "Arraba"}
    out = explode_extraction(ext)
    assert len(out) == 1


def test_explode_none_additional_victims() -> None:
    """`null` from a json-repair recovery → treat as empty list."""
    ext = {"victim_name_ar": "بكر ياسين", "additional_victims": None}
    out = explode_extraction(ext)
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Multi-victim — the actual fix
# ---------------------------------------------------------------------------

def test_explode_triple_murder() -> None:
    """The live Arab48 case: 3 named victims in one article (Yasser
    Hujirat, Kamel Hujirat, Khaled Ghadeer). Explode must emit 3 virtual
    records, one per victim, all sharing the parent's article-level
    fields (incident_type, motive, etc.) but each with its own name."""
    ext = {
        "victim_name_ar": "ياسر حجيرات",
        "city": "عرابة",
        "incident_date": "2026-01-07",
        "incident_type": "homicide",
        "motive": "criminal_dispute",
        "additional_victims": [
            {
                "victim_name_ar": "كامل حجيرات",
                "city": "عرابة",
                "incident_date": "2026-01-07",
                "victim_outcome": "died",
            },
            {
                "victim_name_ar": "خالد غدير",
                "city": "عرابة",
                "incident_date": "2026-01-07",
                "victim_outcome": "died",
            },
        ],
    }
    out = explode_extraction(ext)
    assert len(out) == 3
    assert [v["victim_index"] for v in out] == [0, 1, 2]
    names = [v["victim_name_ar"] for v in out]
    assert names == ["ياسر حجيرات", "كامل حجيرات", "خالد غدير"]
    # Article-level fields share across all virtual records.
    for v in out:
        assert v["motive"] == "criminal_dispute"
        assert v["incident_type"] == "homicide"
        # additional_victims must be stripped from every virtual record.
        assert "additional_victims" not in v


def test_explode_summary_article_13_qatla() -> None:
    """The '13 قتيلا منذ بدء العام' week-in-review article: primary
    victim plus 12 additional. Explode must yield 13 records."""
    additional = [
        {"victim_name_ar": f"victim_{i}", "victim_outcome": "died"}
        for i in range(1, 13)
    ]
    ext = {
        "victim_name_ar": "شام شامي",
        "incident_type": "homicide",
        "additional_victims": additional,
    }
    out = explode_extraction(ext)
    assert len(out) == 13
    assert out[0]["victim_name_ar"] == "شام شامي"
    assert out[1]["victim_name_ar"] == "victim_1"
    assert out[12]["victim_name_ar"] == "victim_12"


def test_explode_swaps_per_victim_fields_not_article_fields() -> None:
    """Per-victim fields (name, age, city, incident_date, outcome) on an
    additional_victim override the parent's. Article-level fields
    (suspect_name, motive, evidence) do NOT — they describe the article."""
    ext = {
        "victim_name_ar": "primary",
        "victim_age": 25,
        "city": "Arraba",
        "incident_date": "2026-01-07",
        "suspect_name": "alleged_shooter",   # article-level
        "motive": "dispute",                  # article-level
        "additional_victims": [
            {
                "victim_name_ar": "secondary",
                "victim_age": 40,
                "city": "Tamra",
                "incident_date": "2026-01-09",
            },
        ],
    }
    out = explode_extraction(ext)
    # Primary keeps its own fields
    assert out[0]["victim_name_ar"] == "primary"
    assert out[0]["victim_age"] == 25
    assert out[0]["city"] == "Arraba"
    # Secondary gets per-victim overrides
    assert out[1]["victim_name_ar"] == "secondary"
    assert out[1]["victim_age"] == 40
    assert out[1]["city"] == "Tamra"
    assert out[1]["incident_date"] == "2026-01-09"
    # Both inherit article-level fields
    assert out[0]["suspect_name"] == "alleged_shooter"
    assert out[1]["suspect_name"] == "alleged_shooter"
    assert out[0]["motive"] == "dispute"
    assert out[1]["motive"] == "dispute"


def test_explode_clears_aliases_on_secondary_victims() -> None:
    """victim_aliases on the parent are for the primary victim's name
    variants. Secondaries must not inherit them or we'd cross-pollute
    distinct identities downstream."""
    ext = {
        "victim_name_ar": "primary",
        "victim_aliases": ["pri", "p.r.i."],
        "additional_victims": [{"victim_name_ar": "secondary"}],
    }
    out = explode_extraction(ext)
    assert out[0]["victim_aliases"] == ["pri", "p.r.i."]
    assert out[1]["victim_aliases"] == []


def test_explode_skips_all_null_additional_victim() -> None:
    """The LLM sometimes emits null placeholders inside additional_victims
    (observed: '[null, null, null, {...}, {...}]' for week-in-review
    articles where it's uncertain about victim count).

    These null entries are dicts with every name field null. If we let
    them through, they become name-less virtual records in dedup, and
    the ``either_name_missing`` merge rule then collapses them with any
    high-cosine pair — bridging unrelated victim clusters into one.

    Concrete pathology (observed Jan 2026 truth investigation): an Azmi
    Ghreib week-in-review article emitted ``additional_victims=[null,
    null, null, {...mahmoud}, {...karam}]``. The three null records
    became bridges that pulled Yasser Hujirat's cluster and Karam
    Swaed's cluster into one canonical case, with 4 distinct victims
    collapsed under one primary name.
    """
    ext = {
        "victim_name_he": "primary",
        "city": "Shfa-Amer",
        "additional_victims": [
            {"victim_name_he": None, "victim_name_ar": None,
             "victim_name_en": None, "victim_name": None},
            {"victim_name_he": "  ", "victim_name_ar": ""},  # whitespace-only
            {"victim_name_he": "secondary"},
        ],
    }
    out = explode_extraction(ext)
    # 1 primary + 1 valid additional (the secondary). 2 null-name entries skipped.
    assert len(out) == 2
    assert out[0]["victim_name_he"] == "primary"
    assert out[1]["victim_name_he"] == "secondary"
    # victim_index must be sequential ignoring the skipped entries.
    assert out[1]["victim_index"] == 1


def test_explode_malformed_additional_victim_entry_is_skipped() -> None:
    """If one entry in additional_victims is not a dict (LLM glitch),
    skip it gracefully without losing the others."""
    ext = {
        "victim_name_ar": "primary",
        "additional_victims": [
            {"victim_name_ar": "good"},
            "garbage_string",  # malformed
            {"victim_name_ar": "another_good"},
        ],
    }
    out = explode_extraction(ext)
    # 1 primary + 2 valid additionals — the string entry is dropped.
    assert len(out) == 3
    assert [v["victim_name_ar"] for v in out] == [
        "primary", "good", "another_good",
    ]


# ---------------------------------------------------------------------------
# victim_count() helper
# ---------------------------------------------------------------------------

def test_victim_count_single() -> None:
    assert victim_count({"victim_name_ar": "a"}) == 1
    assert victim_count({"additional_victims": []}) == 1
    assert victim_count({"additional_victims": None}) == 1


def test_victim_count_multi() -> None:
    assert victim_count({
        "additional_victims": [{"victim_name_ar": "a"}, {"victim_name_ar": "b"}]
    }) == 3


# ---------------------------------------------------------------------------
# Pydantic schema integration
# ---------------------------------------------------------------------------

def test_extracted_article_data_accepts_additional_victims() -> None:
    """The Pydantic model must accept and validate additional_victims."""
    obj = ExtractedArticleData(
        victim_name_ar="primary",
        additional_victims=[
            AdditionalVictim(victim_name_ar="secondary", victim_outcome="died"),
        ],
    )
    assert len(obj.additional_victims) == 1
    assert obj.additional_victims[0].victim_name_ar == "secondary"


def test_extracted_article_data_defaults_empty_list() -> None:
    """Missing additional_victims must default to [], never None.
    This keeps single-victim consumers safe — no NoneType iteration."""
    obj = ExtractedArticleData(victim_name_ar="solo")
    assert obj.additional_victims == []


def test_additional_victim_partial_date_coercion() -> None:
    """AdditionalVictim must accept LLM partial-date glitches the same
    way ExtractedArticleData does (coerce to None instead of rejecting)."""
    av = AdditionalVictim(victim_name_ar="x", incident_date="2026-01-XX")
    assert av.incident_date is None
