"""
Export canonical case records to JSON files plus a per-run manifest.

Outputs are written under a configurable output directory. File names are namespaced
by pipeline run id so concurrent runs do not overwrite each other:

    {output_dir}/{run_id}.json             - SINGLE consolidated rich JSON
                                             (run metadata + stats + cases with
                                              media/media_evidence + human summary)

Schema 2.0 (current): one self-describing JSON per run.
Schema 1.0 helpers (export_case / export_cases / export_manifest /
export_summary) are retained for backward compat with any external callers
but are no longer invoked by the main Pipeline.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from crime_pipeline.models import CanonicalCaseSchema

log = structlog.get_logger()

SCHEMA_VERSION = "2.0"


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

    # ------------------------------------------------------------------
    # Schema 2.0 — single consolidated rich JSON
    # ------------------------------------------------------------------

    def export_run(
        self,
        run_id: str,
        cases: list[CanonicalCaseSchema],
        stats: dict[str, Any],
        human_summary: Optional[str] = None,
    ) -> Path:
        """Write ONE rich JSON containing everything for this run.

        Output: ``{output_dir}/{run_id}.json``

        Top-level shape::

            {
              "schema_version": "2.0",
              "kind": "crime_pipeline.run",
              "pipeline_run_id": "...",
              "exported_at": "...",
              "run": { "started_at", "finished_at", "duration_seconds",
                       "stages_executed" },
              "stats": { discovered, fetched, extracted, clusters, singletons,
                         cases_exported, media_canonical,
                         media_evidence_canonical, total_input_tokens,
                         total_output_tokens, ... },
              "case_count": N,
              "cases": [ <CanonicalCaseSchema dict with media + media_evidence
                          + sources + conflicts + confidence per category> ],
              "human_summary": "..."   (optional plain-text)
            }

        Each case in ``cases`` carries its own ``media`` (decorative),
        ``media_evidence`` (evidentiary), ``sources``, ``conflicts``, and
        per-category ``confidence`` — the case is already self-contained.
        This wrapper adds the run-level provenance so the file is fully
        self-describing without needing the database.
        """
        path = self.output_dir / f"{run_id}.json"

        # Pull run-level timing out of stats, leave the rest in stats.
        started_at = stats.get("started_at")
        finished_at = stats.get("finished_at")
        duration_seconds = self._duration_seconds(started_at, finished_at)

        # The "run" block is provenance only; "stats" is everything else.
        run_block: dict[str, Any] = {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": duration_seconds,
        }
        if "stages_executed" in stats:
            run_block["stages_executed"] = stats["stages_executed"]

        # Strip from stats the keys we promoted to the run block or top level.
        _top_level_keys = {"started_at", "finished_at", "stages_executed", "run_id", "review_pair_details"}
        stats_block = {k: v for k, v in stats.items() if k not in _top_level_keys}

        envelope: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": "crime_pipeline.run",
            "pipeline_run_id": run_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "run": run_block,
            "stats": stats_block,
            "case_count": len(cases),
            "cases": [c.model_dump(mode="json") for c in cases],
            "review_pairs": stats.get("review_pair_details", []),
        }
        if human_summary is not None:
            envelope["human_summary"] = human_summary

        with path.open("w", encoding="utf-8") as f:
            json.dump(
                envelope,
                f,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )

        log.info(
            "run_exported",
            path=str(path),
            cases=len(cases),
            schema_version=SCHEMA_VERSION,
        )
        return path

    @staticmethod
    def _duration_seconds(
        started_at: Optional[str], finished_at: Optional[str]
    ) -> Optional[float]:
        """ISO-8601 strings → elapsed seconds, or None if either is missing."""
        if not started_at or not finished_at:
            return None
        try:
            t0 = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            return (t1 - t0).total_seconds()
        except (ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # Schema 1.0 helpers — retained for backward compat
    # ------------------------------------------------------------------

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
