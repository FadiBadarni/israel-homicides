"""Aggregate validated 2026 cases into a single Schema 2.0 envelope.

Pulls every per-run output JSON in ``output/`` from the validated date
window (Jan 1 → Feb 16, 2026), filters to:

  - victim_outcome == 'died'
  - named (at least one of victim_name_ar / he / en is populated)
  - incident_date inside the 2026 YTD validated window
  - ``incident_geography`` in {israel_arab_society}
    (also accepts ``unknown`` and legacy ``None`` from extractions made
     before the geography field existed, treating those as "uncertain →
     keep for human review")

Then runs ``reconcile_cases`` (which uses the post-fix matcher with
fuzzy per-token containment + the gazetteer additions) to collapse
cross-source duplicates.

Writes a Schema 2.0 envelope to ``output/validated_2026_ytd.json``
which the UI API picks up automatically as one more "run" to browse.

History note: previous revisions of this script had a sprawling
``_NON_ARAB_SOCIETY_NAMES`` + ``_NON_ISRAEL_CITY_HINTS`` blocklist that
grew every time a foreign-news article leaked into the dataset. The
blocklist was replaced by the LLM-driven ``incident_geography`` field
on ``ExtractedArticleData`` — the model is already reading the article
body, it has all the context to make this call directly. One
declarative filter replaces a maintenance treadmill.

Run from project root:
    python scripts/build_validated_2026.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from crime_pipeline.enrichment.reconciler import reconcile_cases


# Date window we've actually validated against ground truth.
DATE_FROM = "2026-01-01"
DATE_TO = "2026-02-16"


# Per-run output files we trust. Includes the original Jan keyword sweeps,
# the Makan + Walla extension runs, and the Feb 1-16 sweep.
_SOURCE_RUNS = [
    # January 2026
    "kw_ar_fa494eb6_2026.json",
    "kw_ar_7612246f_2026.json",
    "kw_ar_a3f907a1_2026.json",
    "kw_ar_534efab3_2026.json",
    "kw_he_737d3b05_2026.json",
    "kw_he_757747d2_2026.json",
    "kw_he_bf66b7f1_2026.json",
    "kw_he_1ebeb661_2026.json",
    "makan_qatl_jan26.json",
    "walla_jan26_rtsh.json",
    "walla_basma.json",
    # February 1-16
    "feb26_he_ynet_1ebeb661.json",
    "feb26_he_walla_1ebeb661.json",
    "feb26_he_ynet_bf66b7f1.json",
    "feb26_he_walla_bf66b7f1.json",
    "feb26_he_ynet_757747d2.json",
    "feb26_he_walla_757747d2.json",
    "feb26_he_ynet_737d3b05.json",
    "feb26_he_walla_737d3b05.json",
    "feb26_ar_arab48_7612246f.json",
    "feb26_ar_makan_7612246f.json",
    "feb26_ar_arab48_a3f907a1.json",
    "feb26_ar_makan_a3f907a1.json",
    "feb26_ar_arab48_534efab3.json",
    "feb26_ar_makan_534efab3.json",
    "feb26_ar_arab48_fa494eb6.json",
    "feb26_ar_makan_fa494eb6.json",
]


# Geography values that PASS the dataset filter. ``israel_arab_society``
# is the target. ``unknown`` and ``None`` (legacy / pre-geography
# extractions) pass through so we don't silently lose Jan cases that
# were extracted before the field existed — they'll get the geography
# label on the next full re-extraction.
_ALLOWED_GEOGRAPHIES = {"israel_arab_society", "unknown", None}


def _parse_date(raw):
    if not raw:
        return None
    try:
        from datetime import date as _date
        return _date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


def _best_name(case: dict) -> str:
    return (
        case.get("victim_name_ar")
        or case.get("victim_name_he")
        or case.get("victim_name_en")
        or case.get("victim_name")
        or ""
    ).strip()


def _is_in_window(case: dict) -> bool:
    from datetime import date as _date
    from_d = _date.fromisoformat(DATE_FROM)
    to_d = _date.fromisoformat(DATE_TO)
    d = _parse_date(case.get("incident_date"))
    return d is not None and from_d <= d <= to_d


def _geography_passes(case: dict) -> bool:
    """The single declarative filter that replaces the old name+city
    blocklists. ``incident_geography`` is set by the LLM at extraction
    time based on the full article body. Legacy ``None`` values pass so
    we don't silently lose pre-geography-field extractions; they'll get
    re-classified on the next extraction run."""
    return case.get("incident_geography") in _ALLOWED_GEOGRAPHIES


def _backfill_geography_from_db(cases: list[dict], db_path: str) -> int:
    """For cases whose ``incident_geography`` is None (older per-run
    JSONs from before the field existed), look up the most-recent
    successful extraction in the DB and copy its geography over.

    Joins by source URL → article_id → latest extracted_records row.
    Returns the number of cases backfilled.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        backfilled = 0
        for case in cases:
            if case.get("incident_geography") is not None:
                continue
            urls = [
                s.get("url")
                for s in (case.get("sources") or [])
                if s.get("url")
            ]
            if not urls:
                continue
            placeholders = ",".join("?" * len(urls))
            cur.execute(
                f"""
                SELECT e.extracted_json
                FROM raw_articles r
                JOIN extracted_records e ON e.article_id = r.id
                WHERE r.url IN ({placeholders})
                  AND e.extraction_status = 'success'
                ORDER BY e.extracted_at DESC
                LIMIT 1
                """,
                urls,
            )
            row = cur.fetchone()
            if not row:
                continue
            data = json.loads(row[0] or "{}")
            geo = data.get("incident_geography")
            if geo is not None:
                case["incident_geography"] = geo
                backfilled += 1
        return backfilled
    finally:
        conn.close()


def main() -> None:
    output_dir = Path("output")
    all_cases: list[dict] = []
    seen_files = []
    missing_files = []

    for fname in _SOURCE_RUNS:
        path = output_dir / fname
        if not path.exists():
            missing_files.append(fname)
            continue
        seen_files.append(fname)
        env = json.loads(path.read_text(encoding="utf-8"))
        all_cases.extend(env.get("cases", []))

    print(f"Loaded {len(seen_files)} run files ({len(missing_files)} missing)")
    if missing_files:
        print(f"  Missing: {missing_files[:5]}...")
    print(f"Raw cases pooled: {len(all_cases)}")

    # Backfill ``incident_geography`` from the DB for cases whose per-run
    # JSONs were written before the field existed. The DB has the latest
    # extraction; this saves us re-running 28 per-run pipelines just to
    # pick up the new field on canonical cases.
    from crime_pipeline.config import Settings
    backfilled = _backfill_geography_from_db(all_cases, str(Settings().db_path))
    print(f"Backfilled incident_geography on {backfilled} cases from DB")

    # Single declarative filter: died + named + in window + geography passes.
    filtered = [
        c for c in all_cases
        if c.get("victim_outcome") == "died"
        and _best_name(c)
        and _is_in_window(c)
        and _geography_passes(c)
    ]
    print(f"After filters (died/named/in-window/geography): {len(filtered)}")

    # Breakdown by geography for visibility — should be all
    # israel_arab_society in steady state, with legacy None during the
    # migration period.
    from collections import Counter
    geo_counts = Counter(c.get("incident_geography") for c in filtered)
    for geo, n in geo_counts.most_common():
        print(f"  geography={geo!r}: {n}")

    # Reconcile across runs — uses the post-fix matcher.
    result = reconcile_cases(filtered, jaro_threshold=0.85)
    print(f"After cross-source reconcile: {result.cases_after} cases "
          f"({len(result.merged_pairs)} merges collapsed duplicates)")

    # Aliases cleanup (positional-anchor — see build history): strip
    # aliases that don't share first AND last tokens with any primary
    # name. Kills father-vs-son name bleed.
    from crime_pipeline.dedup.name_normalizer import (
        jaro_winkler_similarity, romanize_name,
    )

    def _alias_belongs_to_primary(ar: str, primaries_rom: list[str]) -> bool:
        for p in primaries_rom:
            if ar == p:
                return True
            if jaro_winkler_similarity(ar, p) >= 0.95:
                return True
            a_tokens = [t for t in ar.split() if len(t) > 1]
            p_tokens = [t for t in p.split() if len(t) > 1]
            if not a_tokens or not p_tokens:
                continue
            first_jaro = jaro_winkler_similarity(a_tokens[0], p_tokens[0])
            if first_jaro < 0.85:
                continue
            all_match = all(
                any(jaro_winkler_similarity(at, pt) >= 0.85 for pt in p_tokens)
                for at in a_tokens
            )
            if all_match:
                return True
        return False

    def _strip_cross_victim_aliases(case: dict) -> dict:
        primaries_rom = []
        for k in ("victim_name", "victim_name_ar",
                  "victim_name_he", "victim_name_en"):
            v = case.get(k)
            if v:
                primaries_rom.append(romanize_name(v))
        primaries_rom = [p for p in primaries_rom if p]
        if not primaries_rom:
            return case
        clean = []
        dropped = []
        for alias in case.get("aliases") or []:
            ar = romanize_name(alias)
            if not ar:
                continue
            if _alias_belongs_to_primary(ar, primaries_rom):
                clean.append(alias)
            else:
                dropped.append(alias)
        case["aliases"] = clean
        if dropped:
            case.setdefault("flags", []).append("aliases_cleaned")
        return case

    cleaned_cases = [_strip_cross_victim_aliases(c) for c in result.cases]
    stripped = sum(
        1 for c in cleaned_cases if "aliases_cleaned" in (c.get("flags") or [])
    )
    print(f"Aliases cleanup: stripped cross-victim aliases on {stripped} cases")

    # Sort cases by incident_date for the UI.
    def sort_key(c):
        d = _parse_date(c.get("incident_date"))
        return (d.isoformat() if d else "9999", _best_name(c))
    sorted_cases = sorted(cleaned_cases, key=sort_key)

    envelope = {
        "schema_version": "2.0",
        "kind": "crime_pipeline.run",
        "pipeline_run_id": "validated_2026_ytd",
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "run": {
            "started_at": None,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
            "duration_seconds": None,
            "stages_executed": ["aggregate", "filter", "reconcile"],
        },
        "stats": {
            "source_runs_aggregated": len(seen_files),
            "raw_cases_pooled": len(all_cases),
            "after_filters": len(filtered),
            "after_reconcile": result.cases_after,
            "reconcile_merges": len(result.merged_pairs),
            "geography_breakdown": dict(geo_counts),
        },
        "case_count": len(sorted_cases),
        "cases": sorted_cases,
        "human_summary": (
            f"Validated 2026 Arab-society homicide victims, "
            f"{DATE_FROM} to {DATE_TO}. Aggregates {len(seen_files)} per-run "
            f"outputs (Ynet + Arab48 + Makan + Walla), filters to died + "
            f"named + in-window + LLM-classified Israeli Arab society."
        ),
    }

    out_path = output_dir / "validated_2026_ytd.json"
    out_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print()
    print(f"Wrote: {out_path}")
    print(f"  case_count: {envelope['case_count']}")
    print()
    print("Open the UI to validate:")
    print("  1) Terminal A:  uvicorn ui.api.main:app --reload --port 8001")
    print("  2) Terminal B:  cd ui/frontend && npm run dev")
    print("  3) Browser:     http://localhost:3000  → pipeline_run_id 'validated_2026_ytd'")


if __name__ == "__main__":
    main()
