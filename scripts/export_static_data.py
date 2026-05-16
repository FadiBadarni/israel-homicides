"""Export the FastAPI endpoints as static JSON files.

Mirrors GET /api/memorial and GET /api/cases/canonical/{idx} as files
under ``ui/frontend/public/data/`` so the frontend can be served as a
pure static build (Vercel/Netlify/etc) with no backend process.

Outputs:
    ui/frontend/public/data/memorial.json
    ui/frontend/public/data/cases/canonical/{0..N}.json

Run after every dataset change. Idempotent. Free.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from sqlalchemy import select

from crime_pipeline.config import Settings
from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.utils import gazetteer


_CANONICAL_RUN_PREFIX = "canonical_"
_RUN_ID = "canonical"
OUT_DIR = Path("ui/frontend/public/data")
CASES_DIR = OUT_DIR / "cases" / _RUN_ID


def _load_canonical_snapshot() -> list[dict]:
    """Exact mirror of ui/api/main.py::_load_canonical_snapshot."""
    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        rows = list(
            session.scalars(
                select(CanonicalCase)
                .where(CanonicalCase.pipeline_run_id.like(f"{_CANONICAL_RUN_PREFIX}%"))
                .order_by(CanonicalCase.updated_at.desc())
            )
        )
    seen: set[str] = set()
    cases: list[dict] = []
    for row in rows:
        case = dict(row.case_json or {})
        cid = case.get("canonical_case_id") or row.id
        if cid in seen:
            continue
        seen.add(cid)
        case["_pipeline_run_id"] = row.pipeline_run_id
        cases.append(case)
    cases.sort(key=lambda c: (c.get("incident_date") or ""), reverse=True)
    return cases


def build_memorial_payload(snapshot: list[dict]) -> dict:
    """Exact mirror of ui/api/main.py::get_memorial (no year filter — that's
    applied client-side by the frontend's year scrubber)."""
    if not snapshot:
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

    by_city: dict[str, dict] = {}
    unresolved = 0
    incident_years: list[int] = []
    year_counts: dict[str, int] = {}
    documented_deaths = 0
    age_known_count = 0
    under_40_count = 0

    for idx, case in enumerate(snapshot):
        if case.get("victim_outcome") != "died":
            continue
        date = case.get("incident_date")
        year: int | None = None
        if date and len(str(date)) >= 4:
            try:
                year = int(str(date)[:4])
            except ValueError:
                year = None
        if year is not None:
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
            "run_id": _RUN_ID,
            "victim_name": case.get("victim_name"),
            "victim_name_he": case.get("victim_name_he"),
            "victim_name_ar": case.get("victim_name_ar"),
            "victim_name_en": case.get("victim_name_en"),
            "name_transliterations": case.get("name_transliterations") or [],
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
        "run_id": _RUN_ID,
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


def write_case_files(snapshot: list[dict]) -> int:
    if CASES_DIR.exists():
        shutil.rmtree(CASES_DIR)
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for idx, case in enumerate(snapshot):
        payload = {**case, "run_id": _RUN_ID, "case_index": idx}
        out = CASES_DIR / f"{idx}.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        written += 1
    return written


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)

    print("loading canonical snapshot...")
    snapshot = _load_canonical_snapshot()
    print(f"  {len(snapshot)} cases")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    memorial = build_memorial_payload(snapshot)
    memorial_path = OUT_DIR / "memorial.json"
    memorial_path.write_text(
        json.dumps(memorial, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"wrote {memorial_path}  "
        f"({memorial['total_deaths']} deaths, "
        f"{len(memorial['localities'])} localities, "
        f"{memorial['unresolved_count']} unresolved)"
    )

    n = write_case_files(snapshot)
    print(f"wrote {n} case files to {CASES_DIR}/")

    # Index size sanity check
    total_size = sum(p.stat().st_size for p in OUT_DIR.rglob("*.json"))
    print(f"\ntotal payload size: {total_size / 1024:.1f} KB across {n + 1} files")


if __name__ == "__main__":
    main()
