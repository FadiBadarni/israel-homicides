"""Surface canonical cases that look suspicious for manual review.

Five categories, each free (no LLM):

  1. confidence_score < 0.4
     Likely sparse / single-source / poorly-extracted. May be wrong.

  2. incident_geography != "israel_arab_society"
     Jewish-society or unknown-society leaks. The build_canonical filter
     allows these through if geography is null/unknown — worth verifying.

  3. Missing name in EVERY script (victim_name_ar + _he + _en all empty)
     Should be impossible past the filter; if present, junk row.

  4. victim_outcome != "died"
     Should be impossible past the filter; if present, junk row.

  5. Single-source case whose source title contains a count-rollup marker
     (e.g. ``قتيلا منذ بدء العام``). These cases were inferred from a
     count enumeration with no dedicated article — fragile by construction.

Output:
  * Console summary per category
  * CSV at output/outliers.csv (one row per flagged case)
"""
from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.models import CanonicalCase
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


COUNT_ROLLUP_MARKERS = [
    "قتيلا منذ",
    "قتيلا عربيا",
    "ضحية هذا العام",
    "حصيلة القتلى",
    "מתחילת השנה",
    "הרוגים מתחילת",
]


def _has_any_name(c: dict) -> bool:
    return any(
        c.get(k) for k in
        ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en")
    )


def _is_count_rollup_source(s: dict) -> bool:
    title = (s.get("title") or "")
    return any(m in title for m in COUNT_ROLLUP_MARKERS)


def main() -> None:
    settings = Settings()
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    # Load deduplicated snapshot (one row per canonical_case_id, newest wins)
    with db_module.SessionLocal() as session:
        rows = (
            session.query(CanonicalCase)
            .filter(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .all()
        )

    by_id: dict[str, dict] = {}
    rows_sorted = sorted(rows, key=lambda r: r.updated_at or "", reverse=True)
    for r in rows_sorted:
        cj = r.case_json or {}
        cid = cj.get("canonical_case_id") or r.id
        if cid in by_id:
            continue
        by_id[cid] = {
            "row_id": r.id,
            "run_id": r.pipeline_run_id,
            "confidence_score_db": r.confidence_score,
            **cj,
        }
    cases = list(by_id.values())
    print(f"Loaded {len(cases)} unique canonical cases.\n")

    # 1. Low confidence
    low_conf = [
        c for c in cases
        if (c.get("confidence_score") or c.get("confidence_score_db") or 0.0) < 0.4
    ]

    # 2. Bad geography
    bad_geo = [
        c for c in cases
        if (c.get("incident_geography") or "") not in ("israel_arab_society", "")
    ]
    # Also flag explicit unknowns
    unknown_geo = [
        c for c in cases
        if (c.get("incident_geography") or "") in ("", "unknown")
    ]

    # 3. Missing names
    no_name = [c for c in cases if not _has_any_name(c)]

    # 4. Not "died"
    not_died = [c for c in cases if c.get("victim_outcome") != "died"]

    # 5. Single-source count-rollup-only
    rollup_only = [
        c for c in cases
        if len(c.get("sources") or []) == 1
        and (c.get("sources") or [{}])[0]
        and _is_count_rollup_source((c.get("sources") or [{}])[0])
    ]

    def _report(label: str, items: list[dict]) -> None:
        print(f"=== {label}: {len(items)} ===")
        for c in items[:8]:
            name = (
                c.get("victim_name_ar") or c.get("victim_name_he")
                or c.get("victim_name_en") or c.get("victim_name") or "—"
            )
            conf = c.get("confidence_score") or c.get("confidence_score_db") or 0.0
            print(
                f"  [{c.get('canonical_case_id') or c.get('row_id'):<45}] "
                f"conf={conf:.2f}  geo={c.get('incident_geography') or '—'}  "
                f"out={c.get('victim_outcome') or '—'}  date={c.get('incident_date') or '—'}  "
                f"{name[:35]}"
            )
        if len(items) > 8:
            print(f"  ... and {len(items)-8} more (see CSV)")
        print()

    _report("1. confidence_score < 0.4", low_conf)
    _report("2. incident_geography not 'israel_arab_society'", bad_geo)
    _report("    (subset: unknown/empty geography)", unknown_geo)
    _report("3. No name in any script", no_name)
    _report("4. victim_outcome != 'died'", not_died)
    _report("5. Single-source from a count-rollup article", rollup_only)

    # Union for CSV
    seen_cids = set()
    rows_out = []
    for category, items in [
        ("low_confidence", low_conf),
        ("bad_geography", bad_geo),
        ("no_name", no_name),
        ("not_died", not_died),
        ("rollup_only_single_source", rollup_only),
    ]:
        for c in items:
            cid = c.get("canonical_case_id") or c.get("row_id")
            if cid in seen_cids:
                continue
            seen_cids.add(cid)
            name = (
                c.get("victim_name_ar") or c.get("victim_name_he")
                or c.get("victim_name_en") or c.get("victim_name") or ""
            )
            rows_out.append({
                "category": category,
                "canonical_case_id": cid,
                "row_id": c.get("row_id"),
                "incident_date": c.get("incident_date") or "",
                "city": c.get("city") or "",
                "victim_name": name,
                "incident_geography": c.get("incident_geography") or "",
                "victim_outcome": c.get("victim_outcome") or "",
                "confidence_score": (
                    c.get("confidence_score")
                    or c.get("confidence_score_db") or ""
                ),
                "num_sources": len(c.get("sources") or []),
                "first_source_url": (
                    (c.get("sources") or [{}])[0].get("url") or ""
                ),
                "first_source_title": (
                    (c.get("sources") or [{}])[0].get("title") or ""
                ),
            })

    out_csv = Path("output/outliers.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    if rows_out:
        with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            w.writerows(rows_out)
        print(f"Flagged {len(rows_out)} unique cases. Full list: {out_csv}")
    else:
        print("Nothing flagged.")


if __name__ == "__main__":
    main()
