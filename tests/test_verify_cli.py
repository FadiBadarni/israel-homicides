"""Tests for the verify mode (S3) — truth-vs-pipeline comparison.

The verify path is a thin Click handler over crime_pipeline.verification,
which does the actual matching via the existing is_same_incident() gate
(reused for consistency with the cross-source merge logic).
"""
from __future__ import annotations

import inspect
import json

import pytest
from click.testing import CliRunner

from crime_pipeline.verification import (
    VerifyResult,
    load_pipeline_cases,
    load_truth_jsonl,
    verify_run_against_truth,
)


# ---------------------------------------------------------------------------
# Truth file loading
# ---------------------------------------------------------------------------

def test_load_truth_jsonl_skips_blank_and_comment_lines(tmp_path) -> None:
    p = tmp_path / "truth.jsonl"
    p.write_text(
        '\n'
        '# this is a comment\n'
        '{"city": "Arraba", "incident_date": "2026-01-03"}\n'
        '\n'
        '{"city": "Sakhnin", "incident_date": "2026-04-12"}\n',
        encoding="utf-8",
    )
    rows = load_truth_jsonl(p)
    assert len(rows) == 2
    assert rows[0]["city"] == "Arraba"


def test_load_truth_jsonl_raises_on_invalid_json(tmp_path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text('{not valid json\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_truth_jsonl(p)


def test_load_pipeline_cases_returns_envelope_cases(tmp_path) -> None:
    envelope = {"schema_version": "2.0", "cases": [{"victim_name": "X"}]}
    p = tmp_path / "run.json"
    p.write_text(json.dumps(envelope), encoding="utf-8")
    cases = load_pipeline_cases(p)
    assert len(cases) == 1
    assert cases[0]["victim_name"] == "X"


# ---------------------------------------------------------------------------
# Match logic
# ---------------------------------------------------------------------------

def test_perfect_match_gives_100_percent_precision_and_recall() -> None:
    truth = [
        {"city": "Arraba", "victim_name_ar": "بكر ياسين", "incident_date": "2026-01-03"},
    ]
    cases = [
        {
            "city": "Arraba",
            "city_normalized": {"name_en": "Arraba"},
            "victim_name": "بكر ياسين",
            "victim_name_ar": "بكر ياسين",
            "incident_date": "2026-01-03",
        },
    ]
    result = verify_run_against_truth(truth, cases)
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0
    assert result.true_positive == 1
    assert result.false_negative == 0
    assert result.false_positive == 0


def test_missing_truth_record_is_false_negative() -> None:
    truth = [
        {"city": "Arraba", "victim_name_ar": "بكر ياسين", "incident_date": "2026-01-03"},
        {"city": "Sakhnin", "victim_name_ar": "X Y", "incident_date": "2026-04-12"},
    ]
    cases = [{
        "city": "Arraba",
        "city_normalized": {"name_en": "Arraba"},
        "victim_name_ar": "بكر ياسين",
        "incident_date": "2026-01-03",
    }]
    result = verify_run_against_truth(truth, cases)
    assert result.true_positive == 1
    assert result.false_negative == 1
    assert result.false_positive == 0
    assert result.recall == 0.5
    assert len(result.missing_truth) == 1
    assert result.missing_truth[0]["city"] == "Sakhnin"


def test_extra_pipeline_case_is_false_positive() -> None:
    truth = [
        {"city": "Arraba", "victim_name_ar": "بكر ياسين", "incident_date": "2026-01-03"},
    ]
    cases = [
        {
            "city": "Arraba",
            "city_normalized": {"name_en": "Arraba"},
            "victim_name_ar": "بكر ياسين",
            "incident_date": "2026-01-03",
        },
        {
            # Spurious extra case
            "city": "Tel Aviv",
            "city_normalized": {"name_en": "Tel Aviv"},
            "victim_name_ar": "Z",
            "incident_date": "2026-02-15",
            "victim_outcome": "critical",
            "confidence_score": 0.4,
        },
    ]
    result = verify_run_against_truth(truth, cases)
    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.precision == 0.5
    assert result.recall == 1.0
    assert len(result.extra_pipeline) == 1
    assert result.extra_pipeline[0]["city"] == "Tel Aviv"


def test_empty_truth_and_pipeline_gives_zero_metrics() -> None:
    result = verify_run_against_truth([], [])
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def test_help_lists_verify_flags() -> None:
    from crime_pipeline.__main__ import cli
    result = CliRunner().invoke(cli, ["--help"])
    assert "--verify-truth" in result.output
    assert "--verify-run" in result.output


def test_verify_mode_runs_and_writes_summary(tmp_path) -> None:
    """End-to-end CLI: --verify-truth + --verify-run produces a summary file."""
    truth_path = tmp_path / "truth.jsonl"
    truth_path.write_text(
        '{"city": "Arraba", "victim_name_ar": "بكر ياسين", "incident_date": "2026-01-03"}\n',
        encoding="utf-8",
    )
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps({
            "schema_version": "2.0",
            "cases": [{
                "city": "Arraba",
                "city_normalized": {"name_en": "Arraba"},
                "victim_name_ar": "بكر ياسين",
                "incident_date": "2026-01-03",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    from crime_pipeline.__main__ import cli
    result = CliRunner().invoke(cli, [
        "--verify-truth", str(truth_path),
        "--verify-run", str(run_path),
    ])
    assert result.exit_code == 0, result.output
    assert "Precision" in result.output
    assert "Recall" in result.output

    summary_path = run_path.with_suffix(".verify.json")
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["truth_count"] == 1
    assert summary["pipeline_count"] == 1
    assert summary["precision"] == 1.0
    assert summary["recall"] == 1.0


def test_verify_result_dataclass_has_all_fields() -> None:
    """Belt-and-braces invariant for the dataclass shape."""
    r = VerifyResult(
        truth_count=10, pipeline_count=8, true_positive=7,
        false_negative=3, false_positive=1,
        missing_truth=[], extra_pipeline=[],
    )
    d = r.summary_dict()
    for k in ("truth_count", "pipeline_count", "true_positive",
              "false_negative", "false_positive", "precision", "recall", "f1"):
        assert k in d
