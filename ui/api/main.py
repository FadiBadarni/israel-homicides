"""
FastAPI backend for the crime pipeline case browser.

Reads Schema 2.0 JSON files from the output/ directory and serves them
as a REST API consumed by the Next.js frontend.

Start with:
    uvicorn ui.api.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT / "output"

app = FastAPI(title="Crime Pipeline API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_run(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _list_runs() -> list[Path]:
    """Return all Schema 2.0 run JSON files, newest first."""
    files = [
        p for p in OUTPUT_DIR.glob("*.json")
        if not p.name.startswith("_")
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _case_summary(case: dict, run_id: str, case_index: int) -> dict:
    """Slim projection of a case for the list view."""
    return {
        "case_index": case_index,
        "run_id": run_id,
        "victim_name": case.get("victim_name"),
        "victim_name_ar": case.get("victim_name_ar"),
        "victim_name_he": case.get("victim_name_he"),
        "victim_age": case.get("victim_age"),
        "victim_gender": case.get("victim_gender"),
        "victim_outcome": case.get("victim_outcome"),
        "incident_date": case.get("incident_date"),
        "city": case.get("city"),
        "district": case.get("district"),
        "weapon_type": case.get("weapon_type"),
        "suspect_status": case.get("suspect_status"),
        "legal_status": case.get("legal_status"),
        "confidence_score": case.get("confidence_score"),
        "review_status": case.get("review_status"),
        "source_count": len(case.get("sources", [])),
        "flags": case.get("flags", []),
        "media_count": len(case.get("media", [])) + len(case.get("media_evidence", [])),
        "canonical_case_id": case.get("canonical_case_id"),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/runs")
def list_runs() -> list[dict]:
    """Return metadata for all available pipeline runs."""
    result = []
    for path in _list_runs():
        try:
            data = _load_run(path)
            stats = data.get("stats", {})
            result.append({
                "run_id": data.get("pipeline_run_id", path.stem),
                "file": path.name,
                "case_count": data.get("case_count", len(data.get("cases", []))),
                "exported_at": data.get("exported_at"),
                "stages": stats.get("stages_executed", []),
                "non_fatal_excluded": stats.get("non_fatal_excluded", 0),
                "confidence_avg": round(
                    sum(c.get("confidence_score", 0) for c in data.get("cases", [])) /
                    max(len(data.get("cases", [])), 1),
                    3,
                ),
            })
        except Exception:
            continue
    return result


@app.get("/api/cases")
def list_cases(
    run_id: Optional[str] = Query(None, description="Filter to a specific run"),
    city: Optional[str] = Query(None, description="Filter by city (partial match)"),
    outcome: Optional[str] = Query(None, description="Filter by victim_outcome"),
    search: Optional[str] = Query(None, description="Search victim name"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    review_status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Return a paginated list of case summaries across all runs (or a specific run)."""
    all_cases: list[dict] = []

    run_files = _list_runs()
    if run_id:
        run_files = [p for p in run_files if p.stem == run_id or
                     _load_run(p).get("pipeline_run_id") == run_id]

    for path in run_files:
        try:
            data = _load_run(path)
            rid = data.get("pipeline_run_id", path.stem)
            for idx, case in enumerate(data.get("cases", [])):
                summary = _case_summary(case, rid, idx)
                # Apply filters
                if city and city.lower() not in (summary.get("city") or "").lower():
                    continue
                if outcome and summary.get("victim_outcome") != outcome:
                    continue
                if search:
                    name_fields = [
                        summary.get("victim_name") or "",
                        summary.get("victim_name_ar") or "",
                        summary.get("victim_name_he") or "",
                    ]
                    if not any(search.lower() in n.lower() for n in name_fields):
                        continue
                if summary.get("confidence_score", 0) < min_confidence:
                    continue
                if review_status and summary.get("review_status") != review_status:
                    continue
                all_cases.append(summary)
        except Exception:
            continue

    total = len(all_cases)
    start = (page - 1) * limit
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "cases": all_cases[start : start + limit],
    }


@app.get("/api/cases/{run_id}/{case_index}")
def get_case(run_id: str, case_index: int) -> dict:
    """Return the full case record for a specific case."""
    for path in _list_runs():
        try:
            data = _load_run(path)
            if data.get("pipeline_run_id", path.stem) != run_id:
                continue
            cases = data.get("cases", [])
            if case_index < 0 or case_index >= len(cases):
                raise HTTPException(status_code=404, detail="Case index out of range")
            case = cases[case_index]
            return {**case, "run_id": run_id, "case_index": case_index}
        except HTTPException:
            raise
        except Exception:
            continue
    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")


@app.get("/api/stats")
def get_stats() -> dict:
    """Aggregate stats across all runs."""
    total_cases = 0
    outcomes: dict[str, int] = {}
    cities: dict[str, int] = {}
    years: dict[str, int] = {}

    for path in _list_runs():
        try:
            data = _load_run(path)
            for case in data.get("cases", []):
                total_cases += 1
                o = case.get("victim_outcome") or "unknown"
                outcomes[o] = outcomes.get(o, 0) + 1
                c = case.get("city")
                if c:
                    cities[c] = cities.get(c, 0) + 1
                d = case.get("incident_date")
                if d:
                    yr = str(d)[:4]
                    years[yr] = years.get(yr, 0) + 1
        except Exception:
            continue

    return {
        "total_cases": total_cases,
        "outcomes": outcomes,
        "top_cities": sorted(cities.items(), key=lambda x: -x[1])[:10],
        "by_year": dict(sorted(years.items())),
    }
