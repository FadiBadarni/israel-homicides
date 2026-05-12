"""
FastAPI backend for the crime pipeline case browser.

Reads Schema 2.0 JSON files from the output/ directory and serves them
as a REST API consumed by the Next.js frontend.

Start with:
    uvicorn ui.api.main:app --reload --port 8001
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from crime_pipeline.utils import gazetteer

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
# Mtime-keyed cache — avoids re-reading files that haven't changed
# ---------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _load_run_cached(path_str: str, mtime: float) -> dict:
    """Load a run file; cache key includes mtime so stale cache is never served."""
    with open(path_str, encoding="utf-8") as f:
        return json.load(f)


def _load_run(path: Path) -> dict:
    return _load_run_cached(str(path), path.stat().st_mtime)


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
    """Return metadata for all available pipeline runs, deduplicated by run_id."""
    seen: set[str] = set()
    result = []
    for path in _list_runs():
        try:
            data = _load_run(path)
            run_id = data.get("pipeline_run_id", path.stem)
            if run_id in seen:
                continue
            seen.add(run_id)
            stats = data.get("stats", {})
            result.append({
                "run_id": run_id,
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


@app.get("/api/filters")
def get_filters() -> dict:
    """Return distinct values for all filterable fields from the most recent run."""
    cities: set[str] = set()
    weapon_types: set[str] = set()
    outcomes: set[str] = set()
    review_statuses: set[str] = set()
    districts: set[str] = set()

    run_files = _list_runs()[:1]  # most recent run only

    for path in run_files:
        try:
            data = _load_run(path)
            for case in data.get("cases", []):
                if case.get("city"):
                    cities.add(case["city"])
                if case.get("weapon_type"):
                    weapon_types.add(case["weapon_type"])
                if case.get("victim_outcome"):
                    outcomes.add(case["victim_outcome"])
                if case.get("review_status"):
                    review_statuses.add(case["review_status"])
                if case.get("district"):
                    districts.add(case["district"])
        except Exception:
            continue

    return {
        "cities": sorted(cities),
        "weapon_types": sorted(weapon_types),
        "outcomes": sorted(outcomes),
        "review_statuses": sorted(review_statuses),
        "districts": sorted(districts),
    }


@app.get("/api/cases")
def list_cases(
    city: Optional[str] = Query(None),
    district: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    weapon_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    review_status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    flagged: Optional[bool] = Query(None, description="Filter to flagged cases only"),
    named_only: bool = Query(True, description="Hide cases with no victim name"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    sort_by: str = Query("incident_date", description="Field to sort by"),
    sort_dir: str = Query("desc", description="asc or desc"),
) -> dict:
    """Return a paginated, filtered, sorted list of case summaries."""
    all_cases: list[dict] = []

    for path in _list_runs()[:1]:  # most recent run only
        try:
            data = _load_run(path)
            rid = data.get("pipeline_run_id", path.stem)
            for idx, case in enumerate(data.get("cases", [])):
                summary = _case_summary(case, rid, idx)

                if city and city.lower() not in (summary.get("city") or "").lower():
                    continue
                if district and summary.get("district") != district:
                    continue
                if outcome and summary.get("victim_outcome") != outcome:
                    continue
                if weapon_type and summary.get("weapon_type") != weapon_type:
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
                if date_from and summary.get("incident_date"):
                    if str(summary["incident_date"]) < date_from:
                        continue
                if date_to and summary.get("incident_date"):
                    if str(summary["incident_date"]) > date_to:
                        continue
                if flagged is True and not summary.get("flags"):
                    continue
                if flagged is False and summary.get("flags"):
                    continue
                if named_only and not summary.get("victim_name"):
                    continue

                all_cases.append(summary)
        except Exception:
            continue

    # Sort
    reverse = sort_dir.lower() == "desc"
    all_cases.sort(
        key=lambda c: (c.get(sort_by) is None, c.get(sort_by) or ""),
        reverse=reverse,
    )

    total = len(all_cases)
    start = (page - 1) * limit
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "cases": all_cases[start: start + limit],
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


@app.get("/api/review-pairs")
def get_review_pairs() -> dict:
    """Return review pairs from the latest run that need human adjudication."""
    runs = _list_runs()
    if not runs:
        return {"run_id": None, "pairs": []}
    try:
        data = _load_run(runs[0])
        return {
            "run_id": data.get("pipeline_run_id", runs[0].stem),
            "pairs": data.get("review_pairs", []),
        }
    except Exception:
        return {"run_id": None, "pairs": []}


@app.get("/api/memorial")
def get_memorial(
    year_from: Optional[int] = Query(None, description="Inclusive lower bound on incident year"),
    year_to: Optional[int] = Query(None, description="Inclusive upper bound on incident year"),
) -> dict:
    """Return the memorial payload: died-outcome cases grouped by locality.

    Only the most recent pipeline run contributes. Cases without a gazetteer
    match are counted in `unresolved_count` and excluded from `localities`.
    """
    runs = _list_runs()
    if not runs:
        return {
            "run_id": None,
            "year_range": {"from": None, "to": None},
            "total_deaths": 0,
            "documented_deaths": 0,
            "under_40_pct": 0,
            "unresolved_count": 0,
            "year_counts": {},
            "localities": [],
        }

    run_path = runs[0]
    data = _load_run(run_path)
    run_id = data.get("pipeline_run_id", run_path.stem)

    # Group died cases by canonical city (via the gazetteer)
    by_city: dict[str, dict] = {}
    unresolved = 0
    incident_years: list[int] = []
    year_counts: dict[str, int] = {}
    documented_deaths = 0
    age_known_count = 0
    under_40_count = 0

    for idx, case in enumerate(data.get("cases", [])):
        if case.get("victim_outcome") != "died":
            continue

        # Year filter
        date = case.get("incident_date")
        year: int | None = None
        if date and len(str(date)) >= 4:
            try:
                year = int(str(date)[:4])
            except ValueError:
                year = None
        if year is not None:
            if year_from is not None and year < year_from:
                continue
            if year_to is not None and year > year_to:
                continue
            year_key = str(year)
            year_counts[year_key] = year_counts.get(year_key, 0) + 1

        documented_deaths += 1
        victim_age = case.get("victim_age")
        if isinstance(victim_age, int):
            age_known_count += 1
            if victim_age < 40:
                under_40_count += 1

        raw_city = case.get("city")
        rec = gazetteer.normalize_city(raw_city) if raw_city else None
        if not rec or rec.get("lat") is None or rec.get("lng") is None:
            unresolved += 1
            continue

        canonical = rec.get("name_en") or raw_city
        bucket = by_city.setdefault(canonical, {
            "city": canonical,
            "city_he": rec.get("name_he") or None,
            "city_ar": rec.get("name_ar") or None,
            "lat": rec["lat"],
            "lng": rec["lng"],
            "death_count": 0,
            "most_recent_incident_date": None,
            "deaths": [],
        })
        bucket["death_count"] += 1
        most_recent = bucket["most_recent_incident_date"]
        if date and (most_recent is None or str(date) > most_recent):
            bucket["most_recent_incident_date"] = str(date)
        bucket["deaths"].append({
            "case_index": idx,
            "run_id": run_id,
            "victim_name": case.get("victim_name"),
            "victim_name_he": case.get("victim_name_he"),
            "victim_name_ar": case.get("victim_name_ar"),
            "victim_age": case.get("victim_age"),
            "incident_date": str(date) if date else None,
            "confidence_score": case.get("confidence_score"),
        })
        if year is not None:
            incident_years.append(year)

    localities = sorted(by_city.values(), key=lambda loc: -loc["death_count"])
    total_deaths = sum(loc["death_count"] for loc in localities)
    under_40_pct = (
        round((under_40_count / age_known_count) * 100)
        if age_known_count
        else 0
    )

    return {
        "run_id": run_id,
        "year_range": {
            "from": min(incident_years) if incident_years else None,
            "to": max(incident_years) if incident_years else None,
        },
        "total_deaths": total_deaths,
        "documented_deaths": documented_deaths,
        "under_40_pct": under_40_pct,
        "unresolved_count": unresolved,
        "year_counts": dict(sorted(year_counts.items())),
        "localities": localities,
    }


@app.get("/health")
def health() -> dict:
    run_count = len(_list_runs())
    return {"status": "ok", "run_count": run_count}
