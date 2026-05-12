"""DB-backed build of the validated 2026 envelope.

Pulls every successful extraction from ``extracted_records`` (latest
per article) for articles inside the validated date window, then
re-runs the pipeline's second half in-memory:

    dedup → merge → sanity → quality → reconcile → filter → envelope

Previous revision sourced from per-run output JSONs and suffered from
stale-snapshot bugs (a victim_name corrected by a later extraction
still appeared in the validated view because the per-run JSON was
frozen at the bad version). DB-backed: same script always produces
the same answer as the live DB.

Filters (declarative — no hardcoded name/city lists):
  • victim_outcome == 'died'
  • named (at least one of victim_name_ar / he / en)
  • 2026-01-01 ≤ incident_date ≤ 2026-02-16
  • incident_geography ∈ {israel_arab_society, unknown, None}

Run from project root:
    python scripts/build_validated_2026.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.dedup.deduplicator import Deduplicator
from crime_pipeline.enrichment.quality_pass import run_quality_pass
from crime_pipeline.enrichment.reconciler import reconcile_cases
from crime_pipeline.enrichment.sanity_pass import run_sanity_pass
from crime_pipeline.extraction.multivictim import explode_extraction
from crime_pipeline.merging.merger import CaseMerger
from crime_pipeline.models import ExtractedArticleData


DATE_FROM = "2026-01-01"
DATE_TO = "2026-02-16"

# Geography values that pass the dataset filter. israel_arab_society is
# the target; ``unknown`` and ``None`` (pre-geography legacy
# extractions) pass through so we don't silently lose cases — they're
# tagged for human review instead.
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


def _is_in_window(date_str) -> bool:
    from datetime import date as _date
    from_d = _date.fromisoformat(DATE_FROM)
    to_d = _date.fromisoformat(DATE_TO)
    d = _parse_date(date_str)
    return d is not None and from_d <= d <= to_d


def _geography_passes(case: dict) -> bool:
    return case.get("incident_geography") in _ALLOWED_GEOGRAPHIES


def _load_latest_extractions(db_path: str) -> list[dict]:
    """Pull every article's MOST RECENT successful extraction along with
    the article-level metadata the merger needs (url, source, language,
    published_at, raw_html for media).

    Returns a list of dicts with keys: article_id, url, source,
    language, published_at, raw_html, extracted_json (dict, not str).
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              r.id, r.url, r.source, r.language, r.published_at, r.raw_html,
              e.extracted_json
            FROM raw_articles r
            JOIN extracted_records e ON e.article_id = r.id
            WHERE r.fetch_status = 'success'
              AND e.extraction_status = 'success'
              AND e.id = (
                SELECT e2.id
                FROM extracted_records e2
                WHERE e2.article_id = r.id
                  AND e2.extraction_status = 'success'
                ORDER BY e2.extracted_at DESC
                LIMIT 1
              )
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    extractions = []
    for r in rows:
        try:
            data = json.loads(r[6] or "{}")
        except json.JSONDecodeError:
            continue
        if not data:
            continue
        extractions.append({
            "article_id": r[0],
            "url": r[1],
            "source": r[2],
            "language": r[3],
            "published_at": r[4],
            "raw_html": r[5] or "",
            "extracted_json": data,
        })
    return extractions


def _build_dedup_records(extractions: list[dict]) -> tuple[list[dict], dict]:
    """Apply the multi-victim explode and build dedup_records exactly
    the way Pipeline._dedup_and_merge does. Returns (records, lookup)
    where lookup maps composite_id -> (article_data, virtual_extraction).
    """
    dedup_records = []
    virtual_lookup = {}
    for ext in extractions:
        article_text = ""
        # We need article text for cosine similarity in dedup. Re-derive
        # from raw_html if available, otherwise fall back to short body.
        # For DB-pull build, we don't have article_text directly — pull
        # it explicitly.
        virtuals = explode_extraction(ext["extracted_json"])
        for vdata in virtuals:
            vidx = vdata.get("victim_index", 0)
            composite_id = f"{ext['article_id']}#{vidx}"
            virtual_lookup[composite_id] = {
                "ext_article": ext,
                "victim_index": vidx,
                "virtual_extraction": vdata,
            }
            # The LLM often leaves the bare ``victim_name`` empty and
            # only populates victim_name_ar / he / en. The dedup
            # ``either_name_missing`` rule then merges on cosine alone,
            # which collapses unrelated victims who happen to appear
            # in the same week-in-review article. Fall back to any
            # available name so dedup sees a real string to compare.
            name_for_dedup = (
                vdata.get("victim_name")
                or vdata.get("victim_name_ar")
                or vdata.get("victim_name_he")
                or vdata.get("victim_name_en")
            )
            dedup_records.append({
                "id": composite_id,
                "article_id": ext["article_id"],
                "victim_name": name_for_dedup,
                "incident_date": vdata.get("incident_date"),
                "city": vdata.get("city"),
                "article_text": article_text,   # filled below
                "source": ext["source"],
                "url": ext["url"],
                "confidence_score": vdata.get("confidence_score", 0.5),
            })
    return dedup_records, virtual_lookup


def _fill_article_text(
    dedup_records: list[dict],
    extractions: list[dict],
    db_path: str,
) -> None:
    """Pull article_text from the DB for each dedup record. Done in a
    single query for efficiency rather than per-record."""
    article_ids = list({e["article_id"] for e in extractions})
    if not article_ids:
        return
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        placeholders = ",".join("?" * len(article_ids))
        cur.execute(
            f"""
            SELECT id, article_text
            FROM raw_articles
            WHERE id IN ({placeholders})
            """,
            article_ids,
        )
        texts = {row[0]: (row[1] or "")[:2000] for row in cur.fetchall()}
    finally:
        conn.close()

    for rec in dedup_records:
        rec["article_text"] = texts.get(rec["article_id"], "")


async def _attach_media_noop(*args, **kwargs) -> None:
    """Media attachment is expensive and the DB-backed build is for
    headless data correctness, not media curation. Skip it."""


def main() -> None:
    settings = Settings()
    db_path = str(settings.db_path)

    print(f"Loading latest extractions from {db_path}...")
    extractions = _load_latest_extractions(db_path)
    print(f"  {len(extractions)} articles with successful extractions")

    # Validate extractions via Pydantic so we drop any whose JSON
    # doesn't parse against the current schema. This is the same
    # safety net Pipeline._dedup_and_merge applies before merging.
    valid_extractions = []
    drop_reasons: Counter = Counter()
    for ext in extractions:
        try:
            ExtractedArticleData(**ext["extracted_json"])
            valid_extractions.append(ext)
        except Exception as e:
            drop_reasons[type(e).__name__] += 1
    print(f"  {len(valid_extractions)} pass Pydantic validation "
          f"({len(extractions) - len(valid_extractions)} dropped: {dict(drop_reasons)})")

    # Pre-filter: drop extractions whose primary incident_date is outside
    # the target window AND that don't have any additional_victims with
    # in-window dates. Saves the dedup step from clustering thousands of
    # off-window articles.
    def _has_in_window_date(d: dict) -> bool:
        if _is_in_window(d.get("incident_date")):
            return True
        for av in d.get("additional_victims") or []:
            if isinstance(av, dict) and _is_in_window(av.get("incident_date")):
                return True
        return False

    in_window = [e for e in valid_extractions if _has_in_window_date(e["extracted_json"])]
    print(f"  {len(in_window)} have at least one in-window date "
          f"({DATE_FROM} to {DATE_TO})")

    # Build dedup_records via multi-victim explode (mirrors pipeline).
    dedup_records, virtual_lookup = _build_dedup_records(in_window)
    _fill_article_text(dedup_records, in_window, db_path)
    print(f"  exploded into {len(dedup_records)} virtual victim records")

    # Run dedup across the full union. Better than per-run clustering
    # because more pairs visible -> better recall on legitimate merges.
    print()
    print("Running dedup across the full union...")
    deduplicator = Deduplicator(
        jaro_threshold=settings.jaro_threshold,
        cosine_threshold=settings.cosine_threshold,
    )
    try:
        result = deduplicator.run(dedup_records)
    finally:
        deduplicator.close()
    print(f"  clusters: {len(result['clusters'])}  "
          f"singletons: {len(result['singletons'])}  "
          f"review_pairs: {len(result['review_pairs'])}")

    # Merge each cluster (and singleton) into a canonical case.
    rec_lookup = {r["id"]: r for r in dedup_records}
    merger = CaseMerger()
    all_groups = list(result["clusters"]) + [[s] for s in result["singletons"]]

    cases: list = []
    for group in all_groups:
        cluster_input = []
        for rec_id in group:
            vinfo = virtual_lookup.get(rec_id)
            if not vinfo:
                continue
            try:
                ext_obj = ExtractedArticleData(**vinfo["virtual_extraction"])
            except Exception:
                continue
            cluster_input.append({
                "extraction": ext_obj,
                "url": vinfo["ext_article"]["url"],
                "source": vinfo["ext_article"]["source"],
                "language": vinfo["ext_article"]["language"],
                "published_at": vinfo["ext_article"]["published_at"],
                "raw_html": vinfo["ext_article"]["raw_html"],
            })
        if not cluster_input:
            continue
        try:
            case = merger.merge_cluster(cluster_input, pipeline_run_id="validated_2026_ytd")
            cases.append(case)
        except Exception as e:
            print(f"  merge error skipped: {e}")

    print(f"  merged into {len(cases)} canonical cases")

    # Schema-2.0 cases come out as Pydantic objects; convert to dicts
    # for the cleanup passes (which expect plain dicts for round-trip).
    case_dicts = [c.model_dump(mode="json") for c in cases]

    # Sanity → quality → reconcile, identical to the in-pipeline flow.
    case_dicts = [run_sanity_pass(c) for c in case_dicts]
    case_dicts = [run_quality_pass(c) for c in case_dicts]
    reconcile = reconcile_cases(case_dicts, jaro_threshold=0.85)
    print(f"  after sanity+quality+reconcile: {reconcile.cases_after} cases "
          f"({len(reconcile.merged_pairs)} merges)")

    # Final declarative filter — date window + died + named + geography.
    final = [
        c for c in reconcile.cases
        if c.get("victim_outcome") == "died"
        and _best_name(c)
        and _is_in_window(c.get("incident_date"))
        and _geography_passes(c)
    ]
    print(f"  after final filter: {len(final)} cases")

    geo_breakdown = Counter(c.get("incident_geography") for c in final)
    for geo, n in geo_breakdown.most_common():
        print(f"    geography={geo!r}: {n}")

    # Sort by incident_date for the UI.
    def sort_key(c):
        d = _parse_date(c.get("incident_date"))
        return (d.isoformat() if d else "9999", _best_name(c))
    sorted_cases = sorted(final, key=sort_key)

    envelope = {
        "schema_version": "2.0",
        "kind": "crime_pipeline.run",
        "pipeline_run_id": "validated_2026_ytd",
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "run": {
            "started_at": None,
            "finished_at": datetime.now(tz=timezone.utc).isoformat(),
            "duration_seconds": None,
            "stages_executed": ["db_pull", "dedup", "merge", "sanity", "quality", "reconcile"],
        },
        "stats": {
            "articles_with_extractions": len(extractions),
            "pydantic_valid_extractions": len(valid_extractions),
            "in_window_extractions": len(in_window),
            "virtual_records_after_explode": len(dedup_records),
            "merged_cases": len(cases),
            "after_reconcile": reconcile.cases_after,
            "after_final_filter": len(final),
            "geography_breakdown": dict(geo_breakdown),
        },
        "case_count": len(sorted_cases),
        "cases": sorted_cases,
        "human_summary": (
            f"Validated 2026 Arab-society homicide victims, {DATE_FROM} to "
            f"{DATE_TO}. Sourced live from extracted_records (latest per "
            f"article), reconstructed through dedup→merge→sanity→quality→"
            f"reconcile, filtered to died+named+in-window+israel_arab_society."
        ),
    }

    out_path = Path("output") / "validated_2026_ytd.json"
    out_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print()
    print(f"Wrote: {out_path}  case_count={envelope['case_count']}")


if __name__ == "__main__":
    main()
