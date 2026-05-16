"""Refresh media for canonical cases that currently have no images.

Loads the 120ish cases with empty media + media_evidence lists, then
for EACH source article (regardless of host) clears the media cache,
refetches the HTML if missing, re-harvests + classifies, and runs
finalize. Writes results back to canonical_cases.case_json.

Unlike refresh_arab48_media.py which only handled arab48, this one
covers ynet/walla/makan/kul_alarab/panet too — cases that depend on
those sources for imagery have never been re-harvested with the
patched classifier (bare-form keywords, family-relation guard, etc.).

Free, ~5–10 min.
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
    "Accept-Language": "ar,he,en-US;q=0.7,en;q=0.3",
}
POLITE_DELAY_SEC = 1.0


async def _refetch(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def _build_ctx(case: dict) -> ArticleContext:
    victim_names = [n for n in (
        case.get("victim_name"), case.get("victim_name_ar"),
        case.get("victim_name_he"), case.get("victim_name_en"),
    ) if n]
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
    """Refresh ALL source articles for one case. Returns (refetched, candidates, media_after)."""
    src_urls = [s.get("url") for s in (case.get("sources") or []) if s.get("url")]
    if not src_urls:
        return 0, 0, 0

    ctx = _build_ctx(case)
    media_pipeline.classifier.reset_case_budget()
    all_cands: list[MediaCandidate] = []
    refetched = 0

    with db_module.SessionLocal() as sess:
        # Clear cache for all sources
        arts = list(sess.scalars(select(RawArticle).where(RawArticle.url.in_(src_urls))))
        for art in arts:
            art.media_harvest_json = None
            art.media_harvest_version = None
            flag_modified(art, "media_harvest_json")
        sess.commit()

    # Refetch + harvest each source
    with db_module.SessionLocal() as sess:
        arts = list(sess.scalars(select(RawArticle).where(RawArticle.url.in_(src_urls))))
        for art in arts:
            html = art.raw_html
            if not html:
                html = await _refetch(client, art.url)
                if html:
                    art.raw_html = html
                await asyncio.sleep(POLITE_DELAY_SEC)
            if not html:
                continue
            try:
                cands = await media_pipeline.harvest_one_article(art.url, html, ctx)
            except Exception:
                cands = []
            all_cands.extend(cands)
            art.media_harvest_json = [c.model_dump(mode="json") for c in cands]
            art.media_harvest_version = MEDIA_HARVEST_VERSION
            flag_modified(art, "media_harvest_json")
            refetched += 1
        sess.commit()

    if not all_cands:
        return refetched, 0, 0

    media_canon, evidence_canon = await media_pipeline.finalize(all_cands, ctx)
    case["media"] = [m.model_dump(mode="json") for m in media_canon]
    case["media_evidence"] = [m.model_dump(mode="json") for m in evidence_canon]
    media_total = len(case["media"]) + len(case["media_evidence"])

    with db_module.SessionLocal() as sess:
        live = sess.get(CanonicalCase, row_id)
        if live is not None:
            live.case_json = case
            flag_modified(live, "case_json")
            sess.commit()

    return refetched, len(all_cands), media_total


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    # Load all canonical_* rows, dedupe by canonical_case_id (most-recent wins)
    with db_module.SessionLocal() as sess:
        rows = list(sess.scalars(
            select(CanonicalCase).where(CanonicalCase.pipeline_run_id.like("canonical_%"))
            .order_by(CanonicalCase.updated_at.desc())
        ))
    seen: dict[str, CanonicalCase] = {}
    for r in rows:
        cid = (r.case_json or {}).get("canonical_case_id") or r.id
        if cid not in seen:
            seen[cid] = r

    # Pick died cases with no media AND with at least one source
    targets: list[CanonicalCase] = []
    for r in seen.values():
        cj = r.case_json or {}
        if cj.get("victim_outcome") != "died":
            continue
        if (cj.get("media") or []) or (cj.get("media_evidence") or []):
            continue
        if not (cj.get("sources") or []):
            continue
        targets.append(r)
    print(f"target no-media cases: {len(targets)}")

    media_pipeline = MediaPipeline(MediaSettings())
    timeout = httpx.Timeout(20.0, connect=10.0)
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS)

    recovered = 0
    no_change = 0
    errors = 0
    total_media_added = 0
    try:
        for i, row in enumerate(targets, 1):
            case = dict(row.case_json or {})
            name = (case.get("victim_name_ar") or case.get("victim_name") or "?")[:30]
            try:
                refetched, cand_count, media_after = await _refresh_one_case(
                    row.id, case, media_pipeline, client
                )
            except Exception as e:
                errors += 1
                print(f"  [{i:3d}/{len(targets)}] ERROR: {name}: {e}")
                continue
            if media_after > 0:
                recovered += 1
                total_media_added += media_after
                print(
                    f"  [{i:3d}/{len(targets)}] +{media_after} media  "
                    f"({refetched} refetched, {cand_count} cands)  {name}"
                )
            else:
                no_change += 1
    finally:
        await client.aclose()

    print()
    print("=== Summary ===")
    print(f"  cases processed:      {len(targets)}")
    print(f"  cases recovered:      {recovered}  (now have ≥1 image)")
    print(f"  total media attached: {total_media_added}")
    print(f"  no images available:  {no_change}")
    print(f"  errors:               {errors}")


if __name__ == "__main__":
    asyncio.run(main())
