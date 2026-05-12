"""Pipeline funnel diagnostics — reports per-stage drop-off counts.

Reads the SQLite checkpoint tables directly (no Pipeline instance needed)
and surfaces the funnel:

    discovered → fetched → triage_passed → extraction_success
                                         → extraction_failed

Used by ``python -m crime_pipeline --show-pipeline-funnel <run_id>``.
The CLI flag accepts either an exact ``pipeline_run_id`` or a prefix that
matches multiple runs (e.g. ``kw_ar_`` to see every Arabic sweep), or the
literal ``all`` to dump every run in the DB.

Output is read-only — no schema mutations, no Gemini calls.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

# Default DB path — matches storage.db's default. Resolved lazily so tests
# can monkeypatch the Settings DB path without import-time side effects.
_DEFAULT_DB_PATH = Path("data/pipeline.db")


@dataclass(slots=True)
class FunnelRow:
    """Per-(run_id, source) funnel counts."""

    pipeline_run_id: str
    source: str
    discovered: int
    fetched: int
    triage_passed: int
    triage_dropped: int
    extraction_success: int
    extraction_failed: int

    @property
    def fetch_yield(self) -> float:
        return (self.fetched / self.discovered) if self.discovered else 0.0

    @property
    def triage_yield(self) -> float:
        return (self.triage_passed / self.fetched) if self.fetched else 0.0

    @property
    def extract_yield(self) -> float:
        denom = self.extraction_success + self.extraction_failed
        return (self.extraction_success / denom) if denom else 0.0


def _resolve_db_path() -> Path:
    """Pick the DB path lazily so callers without Settings imported still work."""
    try:
        from crime_pipeline.config import Settings  # local import — heavy module
        path = Path(Settings().database_url.replace("sqlite:///", ""))
        if path.exists():
            return path
    except Exception:
        pass
    return _DEFAULT_DB_PATH


def gather_funnel(run_id_filter: str) -> list[FunnelRow]:
    """Pull funnel counts for matching run(s) from SQLite.

    ``run_id_filter`` semantics:
      • ``"all"`` → every distinct pipeline_run_id in raw_articles
      • Otherwise → SQL LIKE ``<filter>%`` (prefix match)

    Returns one row per (pipeline_run_id, source) pair, sorted by run_id
    then source. Returns [] if nothing matches.
    """
    db_path = _resolve_db_path()
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        if run_id_filter.lower() == "all":
            run_clause = "pipeline_run_id IS NOT NULL"
            params: tuple = ()
        else:
            run_clause = "pipeline_run_id LIKE ?"
            params = (f"{run_id_filter}%",)

        # One pass — counts every funnel stage per (run_id, source).
        cur.execute(
            f"""
            SELECT
              r.pipeline_run_id,
              r.source,
              COUNT(*) AS discovered,
              SUM(CASE WHEN r.fetch_status = 'success' THEN 1 ELSE 0 END) AS fetched,
              SUM(CASE WHEN r.triage_status IN ('yes','maybe') THEN 1 ELSE 0 END) AS triage_passed,
              SUM(CASE WHEN r.triage_status = 'no' THEN 1 ELSE 0 END) AS triage_dropped,
              SUM(CASE WHEN e.extraction_status = 'success' THEN 1 ELSE 0 END) AS ext_success,
              SUM(CASE WHEN e.extraction_status = 'failed'  THEN 1 ELSE 0 END) AS ext_failed
            FROM raw_articles r
            LEFT JOIN extracted_records e ON e.article_id = r.id
            WHERE {run_clause}
            GROUP BY r.pipeline_run_id, r.source
            ORDER BY r.pipeline_run_id, r.source
            """,
            params,
        )
        rows = [
            FunnelRow(
                pipeline_run_id=row[0] or "",
                source=row[1] or "",
                discovered=row[2] or 0,
                fetched=row[3] or 0,
                triage_passed=row[4] or 0,
                triage_dropped=row[5] or 0,
                extraction_success=row[6] or 0,
                extraction_failed=row[7] or 0,
            )
            for row in cur.fetchall()
        ]
        return rows
    finally:
        conn.close()


def format_funnel_as_table(rows: list[FunnelRow]) -> str:
    """Render a compact monospace table. Uses stdlib only (no rich dep).

    Layout matches the Discover-phase recommendation: one row per
    (run_id, source), with conversion percentages in parentheses so
    bottlenecks pop out at a glance.
    """
    if not rows:
        return "(no rows)"

    headers = (
        "RUN_ID", "SOURCE", "DISC", "FETCH", "TRIAGE", "EXT_OK", "EXT_FAIL",
    )
    data = [
        (
            r.pipeline_run_id,
            r.source,
            str(r.discovered),
            f"{r.fetched}({r.fetch_yield:.0%})",
            f"{r.triage_passed}({r.triage_yield:.0%})",
            f"{r.extraction_success}({r.extract_yield:.0%})",
            str(r.extraction_failed),
        )
        for r in rows
    ]
    widths = [
        max(len(headers[i]), max(len(row[i]) for row in data))
        for i in range(len(headers))
    ]
    sep = "  "
    lines = [sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append(sep.join("-" * w for w in widths))
    for row in data:
        lines.append(sep.join(row[i].ljust(widths[i]) for i in range(len(row))))
    return "\n".join(lines)


def format_funnel_as_jsonl(rows: list[FunnelRow]) -> str:
    """One JSON object per line. Suitable for piping into jq."""
    return "\n".join(json.dumps(asdict(r), ensure_ascii=False) for r in rows)
