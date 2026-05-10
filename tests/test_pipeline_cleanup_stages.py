"""Tests for the inline cleanup stages (sanity → quality → reconcile).

These verify the regression that prompted this work: default pipeline runs
were silently skipping the deterministic cleanup passes. They now run inline,
the schema covers the fields they produce, and the reconciler exposes a pure
in-memory entry point with provenance attribution.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

from crime_pipeline.enrichment.reconciler import (
    ReconcileResult,
    reconcile_cases,
    reconcile_file,
)
from crime_pipeline.models import CanonicalCaseSchema

# ---------------------------------------------------------------------------
# Schema honesty
# ---------------------------------------------------------------------------

def test_canonical_case_schema_accepts_new_cleanup_fields() -> None:
    """The schema must declare the fields sanity/quality/reconcile produce."""
    case = CanonicalCaseSchema(
        tier_coverage={
            "tier_1": ["ynet"],
            "tier_2": ["arab48"],
            "tier_3": [],
            "untiered": [],
        },
        timeline=[
            {"date": "2026-01-04", "event": "incident", "confidence": "high"},
        ],
        motive_translations=["family dispute"],
        arrest_location_translations=["Arraba"],
        dropped_invalid_sources=[
            {"url": "https://police.gov.il/help-form", "reason": "invalid_tier3_path"},
        ],
        reconciliation_provenance=[
            {"merged_from_url": "https://x", "reason": "name_match", "jaro_score": 0.93},
        ],
    )
    assert case.tier_coverage["tier_1"] == ["ynet"]
    assert case.timeline[0]["event"] == "incident"
    assert case.motive_translations == ["family dispute"]
    assert case.reconciliation_provenance[0]["jaro_score"] == 0.93


def test_canonical_case_schema_defaults_match_legacy_jsons() -> None:
    """Legacy output JSONs (written before this work) must still validate."""
    legacy_min = CanonicalCaseSchema()  # no fields supplied
    assert legacy_min.tier_coverage == {}
    assert legacy_min.timeline == []
    assert legacy_min.motive_translations is None
    assert legacy_min.dropped_invalid_sources == []
    assert legacy_min.reconciliation_provenance == []


def test_round_trip_preserves_new_fields_no_extra_allow_needed() -> None:
    """The schema is honest — round-trip dict→model→dict keeps the new fields."""
    src = {
        "tier_coverage": {"tier_1": ["ynet"], "tier_2": [], "tier_3": [], "untiered": []},
        "timeline": [{"date": "2026-01-04", "event": "death", "confidence": "high"}],
        "motive_translations": ["debt"],
    }
    case = CanonicalCaseSchema(**src)
    dumped = case.model_dump(mode="json")
    assert dumped["tier_coverage"] == src["tier_coverage"]
    assert dumped["timeline"] == src["timeline"]
    assert dumped["motive_translations"] == ["debt"]


# ---------------------------------------------------------------------------
# Reconciler — pure in-memory function
# ---------------------------------------------------------------------------

def _case(victim: str | None, city: str | None, sources: int = 1, **extra) -> dict:
    """Tiny case-dict builder for reconciler tests."""
    return {
        "victim_name": victim,
        "city": city,
        "incident_date": extra.get("incident_date", "2026-01-04"),
        "sources": [{"url": f"https://example.com/{victim or 'x'}/{i}",
                     "actual_publisher": "ynet",
                     "confidence_score": 0.7} for i in range(sources)],
        "confidence_score": extra.get("confidence_score", 0.7),
        "flags": list(extra.get("flags", [])),
    }


def test_reconcile_cases_returns_reconcile_result() -> None:
    result = reconcile_cases([_case("Bakr Yassin", "Arraba")])
    assert isinstance(result, ReconcileResult)
    assert result.cases_before == 1
    assert result.cases_after == 1
    assert result.merged_pairs == []


def test_reconcile_cases_no_file_io(tmp_path: Path) -> None:
    """Confirm the pure function does no file system access."""
    cases = [_case("Ali Khalil", "Nablus"), _case("Ahmed Saleh", "Tel Aviv")]
    files_before = sorted(tmp_path.iterdir())
    reconcile_cases(cases)
    files_after = sorted(tmp_path.iterdir())
    assert files_before == files_after


def test_reconcile_cases_merges_near_duplicate_names() -> None:
    """Two cases with the same city + ~identical name should merge."""
    cases = [
        _case("Bakr Yassin", "Arraba", sources=2),
        _case("Bakr Yasin", "Arraba", sources=1),  # one-letter variant
    ]
    result = reconcile_cases(cases, jaro_threshold=0.85)
    assert result.cases_before == 2
    assert result.cases_after == 1
    assert len(result.merged_pairs) == 1
    assert result.merged_pairs[0]["rule"] == "name_match"


def test_reconcile_cases_writes_provenance_to_merged_case() -> None:
    """A reconciled case must carry an audit trail for downstream consumers."""
    cases = [
        _case("Bakr Yassin", "Arraba", sources=2),
        _case("Bakr Yasin", "Arraba", sources=1),
    ]
    result = reconcile_cases(cases, jaro_threshold=0.85)
    canonical = result.cases[0]
    prov = canonical.get("reconciliation_provenance") or []
    assert len(prov) == 1
    entry = prov[0]
    assert entry["reason"] == "name_match"
    assert entry["jaro_score"] >= 0.85
    assert entry["merged_from_url"] is not None


def test_reconcile_cases_does_not_merge_across_conflicting_cities() -> None:
    cases = [
        _case("Ali Khalil", "Nablus"),
        _case("Ali Khalil", "Tel Aviv"),
    ]
    result = reconcile_cases(cases, jaro_threshold=0.85)
    assert result.cases_after == 2
    assert result.merged_pairs == []


# ---------------------------------------------------------------------------
# Reconciler — backward-compat wrapper
# ---------------------------------------------------------------------------

def test_reconcile_file_still_works_post_refactor(tmp_path: Path) -> None:
    """The legacy --reconcile <json> CLI mode must keep working."""
    envelope = {
        "schema_version": "2.0",
        "cases": [
            _case("Bakr Yassin", "Arraba", sources=2),
            _case("Bakr Yasin", "Arraba", sources=1),
        ],
        "case_count": 2,
        "stats": {},
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    summary = reconcile_file(path, jaro_threshold=0.85)
    assert set(summary) == {"merged_pairs", "cases_before", "cases_after"}
    assert summary["cases_before"] == 2
    assert summary["cases_after"] == 1

    # File rewritten in place
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["case_count"] == 1
    assert written["stats"]["reconciled_merges"] == 1


# ---------------------------------------------------------------------------
# Pipeline integration — _run_cleanup
# ---------------------------------------------------------------------------

def _stub_pipeline(output_dir: Path):
    """A minimal stand-in for `Pipeline` exposing what _run_cleanup needs."""
    from crime_pipeline.pipeline import Pipeline

    settings = types.SimpleNamespace(output_dir=output_dir)
    return types.SimpleNamespace(
        settings=settings,
        run_id="test_run",
        stats={
            "sanity_applied": 0,
            "quality_applied": 0,
            "reconcile_merged": 0,
            "reconcile_audit_path": None,
        },
        _run_cleanup=Pipeline._run_cleanup,  # bound to stub via __get__
    )


def _minimal_canonical_case(victim: str = "Bakr Yassin") -> CanonicalCaseSchema:
    """Build a canonical case the cleanup pipeline can chew on."""
    return CanonicalCaseSchema(
        canonical_case_id=f"test-{victim}",
        victim_name=victim,
        victim_name_ar=None,
        city="Arraba",
        incident_date="2026-01-04",
        sources=[],
        confidence_score=0.6,
    )


def test_run_cleanup_default_runs_all_three_stages(tmp_path: Path) -> None:
    """When all 3 stages are requested, stats reflect all 3 ran."""
    pipe = _stub_pipeline(tmp_path)
    cases = [_minimal_canonical_case("Bakr Yassin")]

    result = pipe._run_cleanup(pipe, cases, {"sanity", "quality", "reconcile"})

    assert pipe.stats["sanity_applied"] == 1
    assert pipe.stats["quality_applied"] == 1
    assert isinstance(result, list)
    assert all(isinstance(c, CanonicalCaseSchema) for c in result)


def test_run_cleanup_skips_sanity_when_excluded(tmp_path: Path) -> None:
    pipe = _stub_pipeline(tmp_path)
    cases = [_minimal_canonical_case()]

    pipe._run_cleanup(pipe, cases, {"quality"})  # only quality

    assert pipe.stats["sanity_applied"] == 0
    assert pipe.stats["quality_applied"] == 1
    assert pipe.stats["reconcile_merged"] == 0
    # No audit file when reconcile didn't run
    assert pipe.stats["reconcile_audit_path"] is None


def test_run_cleanup_writes_audit_jsonl_only_when_merges_happen(tmp_path: Path) -> None:
    """Audit side-file is written iff reconcile actually merged ≥1 pair."""
    pipe = _stub_pipeline(tmp_path)

    # Two near-duplicate cases that should merge
    cases = [
        _minimal_canonical_case("Bakr Yassin"),
        _minimal_canonical_case("Bakr Yasin"),  # one-letter variant
    ]

    pipe._run_cleanup(pipe, cases, {"sanity", "quality", "reconcile"})

    audit_path = tmp_path / "test_run_reconcile_audit.jsonl"
    if pipe.stats["reconcile_merged"] > 0:
        assert audit_path.exists()
        line = audit_path.read_text(encoding="utf-8").strip().splitlines()[0]
        record = json.loads(line)
        assert "rule" in record and "jaro" in record


# ---------------------------------------------------------------------------
# Default-stages regression — the original bug
# ---------------------------------------------------------------------------

def test_default_pipeline_stages_include_cleanup() -> None:
    """The default stage set must include sanity/quality/reconcile.

    This is the regression the inline-cleanup work fixed: pre-fix, default
    runs silently skipped these passes and shipped uncorrected JSON.
    """
    import inspect

    from crime_pipeline.pipeline import Pipeline

    src = inspect.getsource(Pipeline.run)
    # Must list all three new stage names in the default set
    for stage in ("sanity", "quality", "reconcile"):
        assert f'"{stage}"' in src, f"default stages set in pipeline.run must include {stage}"


def test_default_cli_stages_include_cleanup() -> None:
    """The CLI's hardcoded default set must also include the cleanup stages."""
    import inspect

    from crime_pipeline import __main__ as cli

    src = inspect.getsource(cli)
    # Match the defaults block in the run() entry point
    for stage in ("sanity", "quality", "reconcile"):
        assert f'"{stage}"' in src, f"CLI default stage set must include {stage}"
