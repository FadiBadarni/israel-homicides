"""
Export canonical case records to JSON files plus a per-run manifest.

Outputs are written under a configurable output directory. File names are namespaced
by pipeline run id so concurrent runs do not overwrite each other:

    {output_dir}/{run_id}_canonical.json   - case data (single or envelope of many)
    {output_dir}/{run_id}_manifest.json    - run statistics
    {output_dir}/{run_id}_summary.txt      - optional human-readable summary
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from crime_pipeline.models import CanonicalCaseSchema

log = structlog.get_logger()

SCHEMA_VERSION = "1.0"


def _json_default(obj: Any) -> str:
    """JSON serializer for non-default types (datetime, date, Path)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj).__name__} not serializable")


class JSONExporter:
    """Writes canonical cases, manifests, and summaries to a target directory."""

    def __init__(self, output_dir: Path | str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_case(self, case: CanonicalCaseSchema, run_id: str) -> Path:
        """Write a single canonical case to {output_dir}/{run_id}_canonical.json."""
        path = self.output_dir / f"{run_id}_canonical.json"
        case_dict = case.model_dump(mode="json")
        case_dict["schema_version"] = SCHEMA_VERSION
        case_dict["exported_at"] = datetime.now(timezone.utc).isoformat()
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                case_dict,
                f,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
        log.info(
            "case_exported",
            path=str(path),
            confidence=case.confidence_score,
            review_status=case.review_status,
            flags=case.flags,
        )
        return path

    def export_cases(
        self, cases: list[CanonicalCaseSchema], run_id: str
    ) -> Path:
        """Export multiple canonical cases as a single JSON envelope document."""
        path = self.output_dir / f"{run_id}_canonical.json"
        cases_dicts = [c.model_dump(mode="json") for c in cases]
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "pipeline_run_id": run_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "case_count": len(cases),
            "cases": cases_dicts,
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                envelope,
                f,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
        log.info("cases_exported", count=len(cases), path=str(path))
        return path

    def export_manifest(self, run_id: str, stats: dict) -> Path:
        """Write a run manifest with pipeline statistics and run metadata."""
        path = self.output_dir / f"{run_id}_manifest.json"
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            **stats,
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                manifest,
                f,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
        log.info("manifest_exported", path=str(path))
        return path

    def export_summary(self, run_id: str, summary_text: str) -> Path:
        """Write a plain-text English summary of a canonical case or run."""
        path = self.output_dir / f"{run_id}_summary.txt"
        with path.open("w", encoding="utf-8") as f:
            f.write(summary_text)
        log.info("summary_exported", path=str(path))
        return path
