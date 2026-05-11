"""Regression tests for the reconciler's substring name-matching fix.

Live bug: pipeline produced two records for the same victim that the
reconciler failed to merge:
  - victim_name='بكر ياسين'        city='عرابة البطوف' (no incident_date)
  - victim_name='بكر محمود ياسين'   city='عرابة'        incident_date=2026-01-02

Two fixes layered in:
- A. ``_city_conflicts`` now uses the gazetteer to canonicalize ('عرابة'
  and 'عرابة البطوف' resolve to the same Arraba record) — this was
  the actual cause: the pre-fix reconciler treated the cities as a hard
  conflict before the Jaro name check could run.
- B. ``_token_containment_match`` is added as defense-in-depth for cases
  where Jaro itself falls below the 0.85 threshold due to inserted
  middle-name tokens.
"""
from __future__ import annotations

from crime_pipeline.enrichment.reconciler import (
    _city_conflicts,
    reconcile_cases,
)


# ---------------------------------------------------------------------------
# A. City conflict — gazetteer-aware
# ---------------------------------------------------------------------------

def test_city_conflict_treats_arraba_long_form_as_same() -> None:
    """The Bakr Yassin scenario: bare vs long-form Arraba must NOT conflict."""
    a = {"city": "عرابة"}
    b = {"city": "عرابة البطوف"}
    assert _city_conflicts(a, b) is False


def test_city_conflict_treats_arraba_across_scripts_as_same() -> None:
    a = {"city": "Arraba"}
    b = {"city": "עראבה"}
    assert _city_conflicts(a, b) is False


def test_city_conflict_real_mismatch_still_flagged() -> None:
    """Tel Aviv vs Arraba is a real conflict."""
    a = {"city": "Tel Aviv"}
    b = {"city": "Arraba"}
    assert _city_conflicts(a, b) is True


def test_city_conflict_one_side_null_is_not_a_conflict() -> None:
    """A missing city on either side leaves merging open (existing behavior)."""
    assert _city_conflicts({"city": None}, {"city": "Arraba"}) is False
    assert _city_conflicts({"city": "Arraba"}, {"city": None}) is False
    assert _city_conflicts({"city": None}, {"city": None}) is False


def test_city_conflict_unknown_to_gazetteer_falls_back_to_literal() -> None:
    """Two made-up villages compared literally still flag as conflict."""
    a = {"city": "ZzMadeUpVillageA"}
    b = {"city": "ZzMadeUpVillageB"}
    assert _city_conflicts(a, b) is True


# ---------------------------------------------------------------------------
# End-to-end reconcile — the Bakr Yassin scenario merges now
# ---------------------------------------------------------------------------

def _bakr_pair() -> list[dict]:
    """The actual fragmented pair the live pipeline produced."""
    return [
        {
            "victim_name": "بكر ياسين",
            "victim_name_ar": "بكر ياسين",
            "city": "عرابة البطوف",
            "incident_date": None,
            "victim_outcome": "died",
            "sources": [{"url": "https://www.arab48.com/article-a",
                         "actual_publisher": "arab48", "confidence_score": 0.4}],
            "confidence_score": 0.4,
            "flags": [],
        },
        {
            "victim_name": "بكر محمود ياسين",
            "victim_name_ar": "بكر محمود ياسين",
            "city": "عرابة",
            "incident_date": "2026-01-02",
            "victim_outcome": "died",
            "sources": [{"url": "https://www.arab48.com/article-b",
                         "actual_publisher": "arab48", "confidence_score": 0.6}],
            "confidence_score": 0.6,
            "flags": [],
        },
    ]


def test_bakr_yassin_pair_now_merges() -> None:
    """The headline regression — these two cases must collapse into one."""
    result = reconcile_cases(_bakr_pair(), jaro_threshold=0.85)
    assert result.cases_before == 2
    assert result.cases_after == 1
    assert len(result.merged_pairs) == 1
    # Either rule is acceptable (Jaro alone hits 0.914 once city compares OK)
    assert result.merged_pairs[0]["rule"] in {
        "name_match", "name_token_containment",
    }


def test_bakr_yassin_merge_fills_null_incident_date_from_strong() -> None:
    """After merge, the canonical case should carry the date from the
    record that had one."""
    result = reconcile_cases(_bakr_pair(), jaro_threshold=0.85)
    merged = result.cases[0]
    assert merged["incident_date"] == "2026-01-02"


def test_bakr_yassin_merge_records_provenance() -> None:
    result = reconcile_cases(_bakr_pair(), jaro_threshold=0.85)
    merged = result.cases[0]
    prov = merged.get("reconciliation_provenance") or []
    assert len(prov) >= 1


# ---------------------------------------------------------------------------
# B. Token containment — defense in depth for very long middle names
# ---------------------------------------------------------------------------

def test_token_containment_does_not_overmerge_shared_given_name() -> None:
    """`بكر ياسين` (Bakr Yassin) vs `بكر محمد علي` (Bakr Mohammed Ali) —
    same given name but different family. MUST NOT merge."""
    cases = [
        {
            "victim_name_ar": "بكر ياسين",
            "city": "عرابة",
            "sources": [{"url": "x", "actual_publisher": "arab48"}],
            "confidence_score": 0.5,
        },
        {
            "victim_name_ar": "بكر محمد علي",
            "city": "عرابة",
            "sources": [{"url": "y", "actual_publisher": "arab48"}],
            "confidence_score": 0.5,
        },
    ]
    result = reconcile_cases(cases, jaro_threshold=0.85)
    assert result.cases_after == 2


def test_token_containment_does_not_merge_reordered_names() -> None:
    """`X Y` vs `Y X` — token set matches but endpoint guard catches the
    reordering. MUST NOT merge (could be different people in cultures with
    different ordering conventions)."""
    cases = [
        {
            "victim_name_ar": "بكر ياسين",
            "city": "عرابة",
            "sources": [{"url": "x", "actual_publisher": "arab48"}],
            "confidence_score": 0.5,
        },
        {
            "victim_name_ar": "ياسين بكر",
            "city": "عرابة",
            "sources": [{"url": "y", "actual_publisher": "arab48"}],
            "confidence_score": 0.5,
        },
    ]
    result = reconcile_cases(cases, jaro_threshold=0.85)
    # Jaro may or may not catch this depending on token order — but
    # token-containment specifically must NOT merge them.
    # We allow Jaro to merge (it's the high-similarity case) but token
    # containment alone must reject.
    from crime_pipeline.enrichment.reconciler import reconcile_cases as _rc  # noqa
    # The test's point is the endpoint guard works; verify via the rule label
    # only if a merge happened.
    if result.cases_after == 1:
        rule = result.merged_pairs[0]["rule"]
        assert rule != "name_token_containment", (
            "Token containment must not merge reordered names"
        )


def test_token_containment_does_not_merge_single_token_names() -> None:
    """Just `بكر` vs `بكر محمود ياسين` — single-token name on one side.
    The ≥2-token guard prevents merging."""
    cases = [
        {
            "victim_name_ar": "بكر",
            "city": "عرابة",
            "sources": [{"url": "x", "actual_publisher": "arab48"}],
            "confidence_score": 0.5,
        },
        {
            "victim_name_ar": "بكر محمود ياسين",
            "city": "عرابة",
            "sources": [{"url": "y", "actual_publisher": "arab48"}],
            "confidence_score": 0.5,
        },
    ]
    result = reconcile_cases(cases, jaro_threshold=0.99)  # force only containment path
    # Single-token name on one side must NOT trigger containment merge
    # (Jaro may still merge depending on threshold — we set 0.99 to disable it)
    if result.cases_after == 1:
        rule = result.merged_pairs[0]["rule"]
        assert rule != "name_token_containment"
    else:
        assert result.cases_after == 2
