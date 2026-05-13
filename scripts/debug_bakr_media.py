"""One-off: per-article harvest+classify trace for Bakr's case."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from crime_pipeline.config import get_settings
from crime_pipeline.media import ArticleContext, MediaPipeline, MediaSettings
from crime_pipeline.models import RawArticle
from crime_pipeline.storage import db


def main() -> None:
    envelope = json.loads(Path("output/canonical_2026-01-03_2026-01-03.json").read_text(encoding="utf-8"))
    case = envelope["cases"][0]
    sources = case["sources"]
    print(f"Bakr — {len(sources)} sources:")
    for s in sources:
        pub = s.get("source_name") or s.get("actual_publisher") or "?"
        print(f"  [{pub:14}] {s['url'][:90]}")
    print()

    db.init_db(str(get_settings().db_path))
    urls = [s["url"] for s in sources]
    with db.SessionLocal() as session:
        rows = session.execute(
            select(RawArticle.url, RawArticle.raw_html).where(RawArticle.url.in_(urls))
        ).all()
    by_url = {u: h for u, h in rows}

    victim_names = [
        n for n in (
            case.get("victim_name"), case.get("victim_name_ar"),
            case.get("victim_name_he"), case.get("victim_name_en"),
        ) if n
    ]
    city_names = [case.get("city")] if case.get("city") else []
    for k in ("name_ar", "name_he", "name_en"):
        v = (case.get("city_normalized") or {}).get(k)
        if isinstance(v, str) and v not in city_names:
            city_names.append(v)

    mp = MediaPipeline(MediaSettings())
    # Run all 7 articles TOGETHER (same as the pipeline does).
    articles_for_media = []
    for s in sources:
        h = by_url.get(s["url"])
        if h:
            articles_for_media.append({"raw_html": h, "url": s["url"], "article_text": None})
    ctx = ArticleContext(article_url=articles_for_media[0]["url"], victim_names=victim_names, suspect_names=[], city_names=city_names)
    media_canon, evidence_canon = asyncio.run(mp.run_for_case(articles_for_media, ctx))
    print(f"\nFINAL: decorative={len(media_canon)}  evidence={len(evidence_canon)}\n")
    print("ALL ITEMS (sorted by tier/confidence):")
    all_items = list(evidence_canon) + list(media_canon)
    all_items.sort(key=lambda m: (
        0 if m.classifier_tier == "keyword" else 1 if m.classifier_tier == "clip" else 2,
        -(m.confidence or 0),
    ))
    for m in all_items:
        d = m.model_dump(mode="json")
        ev = "EV" if d.get("is_evidence") else "  "
        print(f"  {ev} [{d.get('type','?'):16}] [{d.get('classifier_tier','?'):8}] conf={d.get('confidence',0):.2f} app={d.get('appearance_count')} stock={d.get('is_stock_photo')} | {(d.get('alt_text') or d.get('caption') or '')[:50]}")
        print(f"        why: {d.get('classification_evidence')}")
        print(f"        url: {d.get('primary_url','')[:80]}")


if __name__ == "__main__":
    main()
