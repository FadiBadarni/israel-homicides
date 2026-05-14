"""Backfill ``raw_articles.media_harvest_json`` for articles with raw_html.

Idempotent — articles that already have a cache at the current version are
skipped. Run this once after introducing the per-article media cache;
subsequent ``build_canonical`` runs will populate the cache opportunistically.

After this completes, ``scripts/null_raw_html.py`` can safely null out
``raw_html`` for cached rows to reclaim ~90% of DB weight.

Usage::

    python scripts/backfill_media_cache.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select, update

from crime_pipeline.config import Settings
from crime_pipeline.media.classifier import ArticleContext
from crime_pipeline.media.pipeline import MEDIA_HARVEST_VERSION, MediaPipeline
from crime_pipeline.media.settings import MediaSettings
from crime_pipeline.models import RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


async def backfill(limit: int | None = None, dry_run: bool = False) -> dict[str, int]:
    """Harvest+download+classify every article that needs a cache entry.

    Articles with ``media_harvest_version == MEDIA_HARVEST_VERSION`` are
    treated as up-to-date and skipped. Articles with no raw_html are also
    skipped (they have nothing to harvest from).
    """
    settings = Settings()
    init_db(settings.db_path)

    # Use the same MediaSettings as the main pipeline.
    media_settings = MediaSettings()
    pipe = MediaPipeline(media_settings)

    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        q = select(RawArticle).where(
            (RawArticle.media_harvest_version != MEDIA_HARVEST_VERSION)
            | (RawArticle.media_harvest_version.is_(None))
        )
        if limit is not None:
            q = q.limit(limit)
        articles = list(session.scalars(q))

    print(f"Articles to backfill: {len(articles)}", flush=True)
    if dry_run:
        for a in articles[:10]:
            print(f"  would harvest: {a.id[:8]}  {a.url[:80]}", flush=True)
        return {"would_backfill": len(articles)}

    counts = {"harvested": 0, "empty_html": 0, "no_candidates": 0, "errors": 0}

    for i, article in enumerate(articles, start=1):
        if not article.raw_html:
            counts["empty_html"] += 1
            continue
        # Per-article context: the harvester filters work without
        # case-specific victim/city signals (those only affect the
        # case-level precision_mode prefilter in finalize, which we don't
        # run here). Pass an empty ctx — same posture as a fresh harvest.
        ctx = ArticleContext(article_url=article.url)
        pipe.classifier.reset_case_budget()
        try:
            cands = await pipe.harvest_one_article(article.url, article.raw_html, ctx)
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {article.id[:8]}: {e!r}", flush=True)
            counts["errors"] += 1
            continue

        if not cands:
            counts["no_candidates"] += 1
            cands_json: list[dict] = []
        else:
            counts["harvested"] += 1
            cands_json = [c.model_dump(mode="json") for c in cands]

        with db_module.SessionLocal() as session:
            session.execute(
                update(RawArticle)
                .where(RawArticle.id == article.id)
                .values(
                    media_harvest_json=cands_json,
                    media_harvest_version=MEDIA_HARVEST_VERSION,
                )
            )
            session.commit()

        if i % 50 == 0:
            print(
                f"  [{i}/{len(articles)}] "
                f"harvested={counts['harvested']} "
                f"empty={counts['empty_html']} "
                f"no_cands={counts['no_candidates']} "
                f"errors={counts['errors']}",
                flush=True,
            )

    print()
    print(f"Done. {counts}", flush=True)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="Cap articles per run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(backfill(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
