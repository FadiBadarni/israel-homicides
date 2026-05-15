"""Find suspect duplicate canonical cases for manual review.

Surfaces case pairs where reconcile may have over-conservatively kept them
separate — typically same-victim-across-script-or-spelling pairs that
didn't trip the multi-token Jaro≥0.95 city-conflict bypass.

Criteria (per pair within same year):
  * Romanized names Jaro-Winkler ≥ 0.75  (looser than reconciler's 0.85)
  * Incident date within ±3 days
  * Cities map to the same gazetteer entry (via gazetteer.normalize_city)
    OR raw city strings match after Arabic/Hebrew normalization

Output:
  * Console summary by year
  * CSV at output/suspect_duplicates.csv with columns:
    year, jaro, date_a, name_a, city_a, run_id_a, case_a_url,
                date_b, name_b, city_b, run_id_b, case_b_url
"""
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from datetime import date as _date, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

import jellyfish

from crime_pipeline.config import Settings
from crime_pipeline.dedup.name_normalizer import romanize_name
from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.utils import gazetteer


JARO_FLOOR = 0.75
DATE_PROXIMITY_DAYS = 3


def _canonical_city(raw: str | None) -> str | None:
    """Resolve a raw city string to its English gazetteer name (lowercased)."""
    if not raw:
        return None
    rec = gazetteer.normalize_city(raw)
    if rec and rec.get("name_en"):
        return rec["name_en"].lower()
    return raw.strip().lower()


def _parse_date(s: str | None) -> _date | None:
    if not s:
        return None
    try:
        return _date.fromisoformat(s[:10])
    except ValueError:
        return None


def _best_name_jaro(case_a: dict, case_b: dict) -> tuple[float, str, str]:
    """Best Jaro across romanized name variants. Returns (score, name_a, name_b)."""
    def _all_names(c: dict) -> list[str]:
        out = []
        for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
            v = (c.get(k) or "").strip()
            if v and v not in out:
                out.append(v)
        for v in (c.get("aliases") or []):
            v = (v or "").strip()
            if v and v not in out:
                out.append(v)
        return out

    best = (0.0, "", "")
    for na in _all_names(case_a):
        ra = romanize_name(na)
        if not ra:
            continue
        for nb in _all_names(case_b):
            rb = romanize_name(nb)
            if not rb:
                continue
            score = jellyfish.jaro_winkler_similarity(ra, rb)
            if score > best[0]:
                best = (score, na, nb)
    return best


def _case_url(case_index_by_id: dict[str, int], case_id: str) -> str:
    """Build a UI link for spot-checking a case."""
    idx = case_index_by_id.get(case_id)
    if idx is None:
        return ""
    return f"http://localhost:3000/cases/canonical/{idx}"


def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    gazetteer._index = {}
    gazetteer.load_gazetteer(Path("data/gazetteer.json"))

    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        rows = (
            session.query(CanonicalCase)
            .filter(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .all()
        )

    # Dedupe to one row per canonical_case_id (newest updated_at wins, mirrors
    # the UI snapshot loader).
    by_id: dict[str, CanonicalCase] = {}
    rows_sorted = sorted(rows, key=lambda r: r.updated_at or "", reverse=True)
    for r in rows_sorted:
        cj = r.case_json or {}
        cid = cj.get("canonical_case_id") or r.id
        if cid in by_id:
            continue
        by_id[cid] = r
    canonical_rows = list(by_id.values())

    # Snapshot order (newest incident first) — for case_index URLs
    snap = sorted(
        canonical_rows,
        key=lambda r: ((r.case_json or {}).get("incident_date") or ""),
        reverse=True,
    )
    case_index_by_id = {
        (r.case_json or {}).get("canonical_case_id") or r.id: i
        for i, r in enumerate(snap)
    }

    # Group by year for O(k²) within year, not O(n²) overall
    by_year: dict[str, list[CanonicalCase]] = defaultdict(list)
    for r in canonical_rows:
        cj = r.case_json or {}
        date = _parse_date(cj.get("incident_date"))
        if not date:
            continue
        by_year[str(date.year)].append(r)

    suspects: list[dict] = []
    for year, cases in sorted(by_year.items()):
        n = len(cases)
        if n < 2:
            continue
        for i in range(n):
            for j in range(i + 1, n):
                a_json = cases[i].case_json or {}
                b_json = cases[j].case_json or {}

                # Same/close incident date
                da = _parse_date(a_json.get("incident_date"))
                db_ = _parse_date(b_json.get("incident_date"))
                if not da or not db_:
                    continue
                if abs((da - db_).days) > DATE_PROXIMITY_DAYS:
                    continue

                # Same gazetteer city (catches script-mismatch like
                # `דליית אל-כרמל` vs `دالية الكرمل`)
                city_a = _canonical_city(a_json.get("city"))
                city_b = _canonical_city(b_json.get("city"))
                if not city_a or not city_b or city_a != city_b:
                    continue

                score, name_a, name_b = _best_name_jaro(a_json, b_json)
                if score < JARO_FLOOR:
                    continue

                suspects.append({
                    "year": year,
                    "jaro": round(score, 3),
                    "date_a": da.isoformat(),
                    "name_a": name_a,
                    "city_a": a_json.get("city") or "",
                    "run_id_a": cases[i].pipeline_run_id,
                    "case_a_id": a_json.get("canonical_case_id") or cases[i].id,
                    "case_a_url": _case_url(
                        case_index_by_id,
                        a_json.get("canonical_case_id") or cases[i].id,
                    ),
                    "date_b": db_.isoformat(),
                    "name_b": name_b,
                    "city_b": b_json.get("city") or "",
                    "run_id_b": cases[j].pipeline_run_id,
                    "case_b_id": b_json.get("canonical_case_id") or cases[j].id,
                    "case_b_url": _case_url(
                        case_index_by_id,
                        b_json.get("canonical_case_id") or cases[j].id,
                    ),
                })

    # Sort highest Jaro first — these are the most-likely duplicates
    suspects.sort(key=lambda d: -d["jaro"])

    print(f"Found {len(suspects)} suspect duplicate pairs:")
    by_year_count: dict[str, int] = defaultdict(int)
    for s in suspects:
        by_year_count[s["year"]] += 1
    for y in sorted(by_year_count):
        print(f"  {y}: {by_year_count[y]}")
    print()

    # First 20 to console for spot-check
    print("=== Top 20 by Jaro ===")
    for s in suspects[:20]:
        print(
            f"  [{s['jaro']:.3f}] {s['year']} {s['city_a'][:15]:15s} "
            f"| {s['name_a'][:25]:25s} ({s['date_a']}) "
            f"VS {s['name_b'][:25]:25s} ({s['date_b']})"
        )

    out_csv = Path("output/suspect_duplicates.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        if suspects:
            w = csv.DictWriter(f, fieldnames=list(suspects[0].keys()))
            w.writeheader()
            w.writerows(suspects)
    print()
    print(f"Full list: {out_csv}")
    print(
        "Review the CSV, mark rows you want deleted, and tell me. I'll "
        "drop the corresponding canonical_cases rows by case_a_id."
    )


if __name__ == "__main__":
    main()
