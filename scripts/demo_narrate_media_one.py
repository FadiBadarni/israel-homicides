"""One-off: run narration + media-fill on a single case for inspection.

Targets the canonical_* row for the supplied canonical_case_id, runs
narrate_cases (generates AR/HE/EN summaries), then re-fetches any
source articles whose raw_html is empty so the media pipeline can
harvest fresh candidates. Prints before/after diffs and writes results
back to the canonical_cases row.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
from pathlib import Path

# Force UTF-8 stdout so Arabic/Hebrew prints don't crash on Windows cp1252.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from crime_pipeline.config import Settings
from crime_pipeline.models import CanonicalCase, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.enrichment.narrator import narrate_cases
from crime_pipeline.media.pipeline import MediaPipeline, MEDIA_HARVEST_VERSION
from crime_pipeline.media.classifier import ArticleContext
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings


CANON_ID = sys.argv[1] if len(sys.argv) > 1 else "IL-HOMICIDE-2026-SHFARAM-NBYL-ABU-JLYL"


def _print_state(label: str, cj: dict) -> None:
    print(f"\n=== {label} ===")
    for lang in ("ar", "he", "en"):
        v = cj.get(f"case_narrative_{lang}") or ""
        print(f"  narrative_{lang}: {v if v else '<empty>'}")
    print(f"  media count: {len(cj.get('media') or [])}")
    print(f"  media_evidence count: {len(cj.get('media_evidence') or [])}")
    for m in (cj.get("media") or []):
        print(
            f"    media: type={m.get('type')} | conf={m.get('confidence'):.2f} | "
            f"url={(m.get('primary_url') or '')[:90]}"
        )
    for m in (cj.get("media_evidence") or []):
        print(
            f"    evidence: type={m.get('type')} | reason={m.get('evidence_reason')} | "
            f"url={(m.get('primary_url') or '')[:90]}"
        )


async def _refetch_article_html(url: str) -> str | None:
    """Live-refetch the article HTML. Picks scraper by host."""
    import httpx
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.text
            print(f"    refetch HTTP {r.status_code} for {url[-60:]}")
        except Exception as e:
            print(f"    refetch failed for {url[-60:]}: {e}")
    return None


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    # 1. Load the latest canonical row for the case
    with db_module.SessionLocal() as sess:
        rows = list(sess.scalars(
            select(CanonicalCase)
            .where(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .order_by(CanonicalCase.updated_at.desc())
        ))
        target_row = None
        for r in rows:
            if (r.case_json or {}).get("canonical_case_id") == CANON_ID:
                target_row = r
                break
    if target_row is None:
        print(f"ERROR: canonical row not found for {CANON_ID}")
        return

    case_dict = dict(target_row.case_json or {})
    target_row_id = target_row.id
    src_urls = [s.get("url") for s in (case_dict.get("sources") or []) if s.get("url")]

    _print_state("BEFORE", case_dict)

    # 2. Narration backfill
    print("\n--- Running narrate_cases ---")
    counter = await narrate_cases(
        [case_dict],
        api_key=settings.gemini_api_key,
        session_factory=db_module.SessionLocal,
        model=settings.llm_model,
        concurrency=1,
    )
    print(f"  narrator counter: {counter}")

    # 3. Media-fill — for each source article with empty raw_html, refetch live
    #    so the MediaPipeline can harvest fresh candidates. Cached candidates
    #    are kept (already produced the existing 1-media result).
    media_settings = MediaSettings()
    media_pipeline = MediaPipeline(media_settings)
    media_pipeline.classifier.reset_case_budget()

    victim_names = [
        n for n in (
            case_dict.get("victim_name"), case_dict.get("victim_name_ar"),
            case_dict.get("victim_name_he"), case_dict.get("victim_name_en"),
        ) if n
    ]
    city_names: list[str] = []
    if case_dict.get("city"):
        city_names.append(case_dict["city"])
    for k in ("name_ar", "name_he", "name_en"):
        v = (case_dict.get("city_normalized") or {}).get(k)
        if isinstance(v, str) and v and v not in city_names:
            city_names.append(v)
    ctx = ArticleContext(
        article_url=src_urls[0] if src_urls else "",
        victim_names=victim_names,
        suspect_names=[case_dict.get("suspect_name")] if case_dict.get("suspect_name") else [],
        city_names=city_names,
    )

    print("\n--- Refetching articles + harvesting media ---")
    all_cands: list[MediaCandidate] = []
    cache_writes: dict[str, list[dict]] = {}

    with db_module.SessionLocal() as sess:
        arts = list(sess.scalars(select(RawArticle).where(RawArticle.url.in_(src_urls))))

        for art in arts:
            cached = art.media_harvest_json
            cached_ver = art.media_harvest_version

            if cached is not None and cached_ver == MEDIA_HARVEST_VERSION and cached:
                try:
                    cands = [MediaCandidate(**d) for d in cached]
                    all_cands.extend(cands)
                    print(f"  cache_hit: {art.url[-60:]} → {len(cands)} candidates")
                except Exception as e:
                    print(f"  cache_deserialize_failed: {art.url[-60:]} ({e})")
                continue

            # No cache (or empty). Try live refetch.
            html = art.raw_html
            if not html:
                print(f"  refetching: {art.url[-60:]}")
                html = await _refetch_article_html(art.url)
                if html:
                    art.raw_html = html  # persist for future
            if not html:
                continue

            cands = await media_pipeline.harvest_one_article(art.url, html, ctx)
            print(f"  harvested: {art.url[-60:]} → {len(cands)} candidates")
            all_cands.extend(cands)
            cache_writes[art.id] = [c.model_dump(mode="json") for c in cands]

        # Persist any new raw_html / media_harvest_json
        if cache_writes:
            for art in arts:
                if art.id in cache_writes:
                    art.media_harvest_json = cache_writes[art.id]
                    art.media_harvest_version = MEDIA_HARVEST_VERSION
        sess.commit()

    print(f"\n  total candidates across sources: {len(all_cands)}")
    if all_cands:
        media_canon, evidence_canon = await media_pipeline.finalize(all_cands, ctx)
        case_dict["media"] = [cm.model_dump(mode="json") for cm in media_canon]
        case_dict["media_evidence"] = [cm.model_dump(mode="json") for cm in evidence_canon]
        print(f"  after finalize: media={len(media_canon)}, evidence={len(evidence_canon)}")

    # 4. Persist updated case_json back to the canonical row
    with db_module.SessionLocal() as sess:
        live = sess.get(CanonicalCase, target_row_id)
        if live is None:
            print("ERROR: row vanished")
            return
        live.case_json = case_dict
        flag_modified(live, "case_json")
        sess.commit()

    # 5. Re-read and print after
    with db_module.SessionLocal() as sess:
        live = sess.get(CanonicalCase, target_row_id)
        _print_state("AFTER", live.case_json or {})


if __name__ == "__main__":
    asyncio.run(main())
