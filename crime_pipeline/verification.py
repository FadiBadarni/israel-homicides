"""Truth-vs-pipeline verification (Strategy C stage 3).

Loads a JSONL ground-truth file and a pipeline output JSON, matches each
truth record to the closest pipeline case via the existing
``is_same_incident()`` gate (Jaro-Winkler ≥ 0.70 + city + ±5-day date
window), and reports precision / recall / F1 plus the false-negative
and false-positive sets.

Truth file format (one JSON object per line)::

    {"city": "Arraba", "victim_name_he": "בכר מחמוד יאסין",
     "victim_name_ar": "بكر ياسين", "incident_date": "2026-01-03"}

Any subset of fields is acceptable — the matcher uses whatever's there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class VerifyResult:
    """Pure-data return from ``verify_run_against_truth``."""

    truth_count: int
    pipeline_count: int
    true_positive: int
    false_negative: int
    false_positive: int
    missing_truth: list[dict[str, Any]]   # cases in truth but not in pipeline
    extra_pipeline: list[dict[str, Any]]  # cases in pipeline but not in truth

    @property
    def precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0

    def summary_dict(self) -> dict[str, Any]:
        return {
            "truth_count": self.truth_count,
            "pipeline_count": self.pipeline_count,
            "true_positive": self.true_positive,
            "false_negative": self.false_negative,
            "false_positive": self.false_positive,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "missing_truth": self.missing_truth,
            "extra_pipeline": self.extra_pipeline,
        }


def load_truth_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read JSONL truth file. Skips blank lines and ``#`` comments."""
    records: list[dict[str, Any]] = []
    p = Path(path)
    for line_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p}:{line_no} invalid JSON: {e}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"{p}:{line_no} expected JSON object, got {type(obj)}")
        records.append(obj)
    return records


def load_pipeline_cases(path: str | Path) -> list[dict[str, Any]]:
    """Read a pipeline output JSON envelope and return its cases list."""
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = envelope.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError(f"{path}: expected envelope['cases'] to be a list")
    return cases


def _truth_to_case_shape(truth: dict[str, Any]) -> dict[str, Any]:
    """Convert a truth record into the dict shape ``is_same_incident``
    expects on its first arg (named like a CanonicalCaseSchema dump)."""
    return {
        "victim_name": truth.get("victim_name") or truth.get("victim_name_he")
                       or truth.get("victim_name_ar") or truth.get("victim_name_en"),
        "victim_name_ar": truth.get("victim_name_ar"),
        "victim_name_he": truth.get("victim_name_he"),
        "victim_name_en": truth.get("victim_name_en"),
        "city": truth.get("city"),
        "city_normalized": truth.get("city_normalized") or {},
        "incident_date": truth.get("incident_date"),
        "aliases": truth.get("aliases") or [],
    }


def verify_run_against_truth(
    truth_records: list[dict[str, Any]],
    pipeline_cases: list[dict[str, Any]],
) -> VerifyResult:
    """Match truth records to pipeline cases.

    Uses the same gating logic as the cross-source merge (jaro≥0.70 on
    romanized names, city normalization via gazetteer, ±5-day date window)
    so the eval rule is consistent with how the pipeline merges incidents.
    Greedy 1:1 matching — once a pipeline case is matched it can't match
    another truth record.
    """
    from crime_pipeline.enrichment.enricher import is_same_incident

    matched_truth_idx: set[int] = set()
    matched_case_idx: set[int] = set()

    for ti, truth in enumerate(truth_records):
        truth_shape = _truth_to_case_shape(truth)
        for ci, case in enumerate(pipeline_cases):
            if ci in matched_case_idx:
                continue
            try:
                ok, _reason = is_same_incident(case, truth_shape)
            except Exception:
                continue
            if ok:
                matched_truth_idx.add(ti)
                matched_case_idx.add(ci)
                break

    tp = len(matched_case_idx)
    fn = len(truth_records) - len(matched_truth_idx)
    fp = len(pipeline_cases) - len(matched_case_idx)

    missing_truth = [
        t for i, t in enumerate(truth_records) if i not in matched_truth_idx
    ]
    extra_pipeline = [
        # Don't dump full case JSON — just a fingerprint
        {
            "victim_name": c.get("victim_name"),
            "victim_name_ar": c.get("victim_name_ar"),
            "city": c.get("city"),
            "incident_date": c.get("incident_date"),
            "outcome": c.get("victim_outcome"),
            "confidence_score": c.get("confidence_score"),
            "flags": c.get("flags"),
        }
        for i, c in enumerate(pipeline_cases) if i not in matched_case_idx
    ]

    return VerifyResult(
        truth_count=len(truth_records),
        pipeline_count=len(pipeline_cases),
        true_positive=tp,
        false_negative=fn,
        false_positive=fp,
        missing_truth=missing_truth,
        extra_pipeline=extra_pipeline,
    )
