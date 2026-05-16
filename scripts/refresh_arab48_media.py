"""One-shot: refresh media for every canonical case with an arab48 source.

Applies four recent harvester/classifier fixes to existing data:
  1. captionless body images (e.g. crime-scene photos with empty alt)
  2. roundup articles where the URL/title doesn't name the specific victim
  3. expanded crime_scene Arabic keyword set
  4. /facebook_waterMark/ play-button overlay → canonical un-watermarked JPG

For each arab48-sourced case:
  - clear the article's cached media_harvest_json + version
  - refetch raw_html live (polite delay between fetches)
  - run the media pipeline (harvest → finalize) on the merged candidate
    set, treating non-arab48 sources as cache hits
  - write the new media + media_evidence back to canonical_cases.case_json

Free (no LLM calls). Estimate ~10-15 min for ~200 cases.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from crime_pipeline.config import Settings
from crime_pipeline.media.classifier import ArticleContext
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.pipeline import MediaPipeline, MEDIA_HARVEST_VERSION
from crime_pipeline.media.settings import MediaSettings
from crime_pipeline.models import CanonicalCase, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.7,en;q=0.3",
}
POLITE_DELAY_SEC = 1.5


async def _refetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def _build_ctx(case: dict) -> ArticleContext:
    victim_names = [
        n for n in (
            case.get("victim_name"), case.get("victim_name_ar"),
            case.get("victim_name_he"), case.get("victim_name_en"),
        ) if n
    ]
    for alias in case.get("aliases") or []:
        if alias and alias not in victim_names:
            victim_names.append(alias)
    city_names: list[str] = []
    if case.get("city"):
        city_names.append(case["city"])
    for k in ("name_ar", "name_he", "name_en"):
        v = (case.get("city_normalized") or {}).get(k)
        if isinstance(v, str) and v and v not in city_names:
            city_names.append(v)
    if case.get("neighborhood") and case["neighborhood"] not in city_names:
        city_names.append(case["neighborhood"])
    return ArticleContext(
        article_url=(case.get("sources") or [{}])[0].get("url", ""),
        victim_names=victim_names,
        suspect_names=[case["suspect_name"]] if case.get("suspect_name") else [],
        city_names=city_names,
    )


async def _refresh_one_case(
    row_id: str,
    case: dict,
    media_pipeline: MediaPipeline,
    client: httpx.AsyncClient,
) -> tuple[int, int, int]:
    """Refresh media for one case. Returns (before_total, after_total, refetched)."""
    src_urls = [s.get("url") for s in (case.get("sources") or []) if s.get("url")]
    arab48_urls = [u for u in src_urls if "arab48.com" in u]
    non_arab48_urls = [u for u in src_urls if "arab48.com" not in u]
    before_total = len(case.get("media") or []) + len(case.get("media_evidence") or [])

    ctx = _build_ctx(case)
    media_pipeline.classifier.reset_case_budget()
    all_cands: list[MediaCandidate] = []
    refetched = 0

    with db_module.SessionLocal() as sess:
        # Clear arab48 caches first
        arab48_arts = list(sess.scalars(
            select(RawArticle).where(RawArticle.url.in_(arab48_urls))
        ))
        for art in arab48_arts:
            art.media_harvest_json = None
            art.media_harvest_version = None
            flag_modified(art, "media_harvest_json")
        sess.commit()

    # Refetch + harvest arab48 sources
    with db_module.SessionLocal() as sess:
        for url in arab48_urls:
            art = sess.scalar(select(RawArticle).where(RawArticle.url == url))
            if art is None:
                continue
            html = art.raw_html
            if not html:
                html = await _refetch(client, url)
                if html:
                    art.raw_html = html
                await asyncio.sleep(POLITE_DELAY_SEC)
            if not html:
                continue
            try:
                cands = await media_pipeline.harvest_one_article(url, html, ctx)
            except Exception:
                cands = []
            all_cands.extend(cands)
            # persist new harvest cache
            art.media_harvest_json = [c.model_dump(mode="json") for c in cands]
            art.media_harvest_version = MEDIA_HARVEST_VERSION
            flag_modified(art, "media_harvest_json")
            refetched += 1
        sess.commit()

    # Reuse cached candidates from non-arab48 sources
    with db_module.SessionLocal() as sess:
        others = list(sess.scalars(
            select(RawArticle).where(RawArticle.url.in_(non_arab48_urls))
        ))
        for art in others:
            cached = art.media_harvest_json
            cached_ver = art.media_harvest_version
            if cached and cached_ver == MEDIA_HARVEST_VERSION:
                try:
                    all_cands.extend(MediaCandidate(**d) for d in cached)
                except Exception:
                    pass

    if not all_cands:
        return before_total, before_total, refetched

    media_canon, evidence_canon = await media_pipeline.finalize(all_cands, ctx)
    case["media"] = [m.model_dump(mode="json") for m in media_canon]
    case["media_evidence"] = [m.model_dump(mode="json") for m in evidence_canon]
    after_total = len(case["media"]) + len(case["media_evidence"])

    with db_module.SessionLocal() as sess:
        live = sess.get(CanonicalCase, row_id)
        if live is not None:
            live.case_json = case
            flag_modified(live, "case_json")
            sess.commit()

    return before_total, after_total, refetched


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    # Collect target rows: every canonical_* row with at least one arab48 source
    with db_module.SessionLocal() as sess:
        rows = list(sess.scalars(
            select(CanonicalCase).where(
                CanonicalCase.pipeline_run_id.like("canonical_%")
            )
        ))

    targets: list[tuple[str, dict]] = []
    for r in rows:
        cj = r.case_json or {}
        urls = [s.get("url") for s in (cj.get("sources") or []) if s.get("url")]
        if any("arab48.com" in u for u in urls):
            targets.append((r.id, cj))
    print(f"arab48-sourced canonical rows: {len(targets)}")

    media_pipeline = MediaPipeline(MediaSettings())
    timeout = httpx.Timeout(20.0, connect=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS)

    total_added = 0
    refetched_total = 0
    errors = 0
    try:
        for i, (row_id, case) in enumerate(targets, 1):
            name = (case.get("victim_name_ar") or case.get("victim_name") or "?")[:32]
            try:
                before, after, refetched = await _refresh_one_case(
                    row_id, case, media_pipeline, client
                )
                delta = after - before
                total_added += delta
                refetched_total += refetched
                if delta > 0:
                    sign = "+"
                elif delta < 0:
                    sign = ""
                else:
                    sign = " "
                print(
                    f"  [{i:3d}/{len(targets)}] {sign}{delta:+2d}  "
                    f"{before:>2}→{after:>2}  refetched={refetched}  {name}"
                )
            except Exception as e:
                errors += 1
                print(f"  [{i:3d}/{len(targets)}] ERROR: {name}: {e}")
    finally:
        await client.aclose()

    print()
    print(f"=== Summary ===")
    print(f"  cases processed:      {len(targets)}")
    print(f"  articles refetched:   {refetched_total}")
    print(f"  total media added:    {total_added}")
    print(f"  errors:               {errors}")


if __name__ == "__main__":
    asyncio.run(main())
