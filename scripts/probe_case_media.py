"""
One-shot media probe: re-run the media pipeline on a single canonical case
and patch the case's media + media_evidence fields in the existing
canonical JSON file. No full pipeline rebuild required.

Usage:
    python scripts/probe_case_media.py --canonical output/canonical_2026-01-03_2026-01-03.json --case-index 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from crime_pipeline.config import get_settings
from crime_pipeline.media import ArticleContext, MediaPipeline, MediaSettings
from crime_pipeline.models import RawArticle
from crime_pipeline.storage import db


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--case-index", type=int, default=0)
    parser.add_argument("--write", action="store_true",
                        help="Patch the canonical JSON in-place with the new media.")
    args = parser.parse_args()

    envelope = json.loads(args.canonical.read_text(encoding="utf-8"))
    case = envelope["cases"][args.case_index]
    name = case.get("victim_name_ar") or case.get("victim_name_he") or "?"
    urls = [s["url"] for s in case.get("sources", [])]
    print(f"CASE: {name}  ({args.case_index})  sources={len(urls)}")

    db.init_db(str(get_settings().db_path))
    with db.SessionLocal() as session:
        rows = session.execute(
            select(RawArticle.url, RawArticle.raw_html).where(RawArticle.url.in_(urls))
        ).all()
    articles_for_media = [
        {"raw_html": h, "url": u, "article_text": None} for (u, h) in rows if h
    ]
    print(f"  raw_html resolved: {len(articles_for_media)}/{len(urls)}")
    if not articles_for_media:
        return 1

    # Same context construction as the (now-fixed) pipeline path.
    victim_names = [
        n for n in (
            case.get("victim_name"), case.get("victim_name_ar"),
            case.get("victim_name_he"), case.get("victim_name_en"),
        ) if n
    ]
    for alias in case.get("aliases") or []:
        if alias and alias not in victim_names:
            victim_names.append(alias)
    suspect_names = [case["suspect_name"]] if case.get("suspect_name") else []
    city_names: list[str] = []
    if case.get("city"):
        city_names.append(case["city"])
    for k in ("name_ar", "name_he", "name_en"):
        v = (case.get("city_normalized") or {}).get(k)
        if isinstance(v, str) and v and v not in city_names:
            city_names.append(v)
    if case.get("neighborhood") and case["neighborhood"] not in city_names:
        city_names.append(case["neighborhood"])

    ctx = ArticleContext(
        article_url=articles_for_media[0]["url"],
        victim_names=victim_names,
        suspect_names=suspect_names,
        city_names=city_names,
    )
    print(f"  victim_names: {victim_names}")
    print(f"  city_names:   {city_names}")

    mp = MediaPipeline(MediaSettings())
    media_canon, evidence_canon = asyncio.run(mp.run_for_case(articles_for_media, ctx))
    print(f"\n  ➜ media (decorative): {len(media_canon)}")
    print(f"  ➜ media_evidence:     {len(evidence_canon)}")

    for i, m in enumerate(evidence_canon[:5]):
        d = m.model_dump(mode="json")
        print(f"\n  EVIDENCE #{i}:")
        print(f"    url:    {d['primary_url'][:90]}")
        print(f"    alt:    {d.get('alt_text')}")
        print(f"    caption:{d.get('caption')}")
        print(f"    tier:   {d.get('classifier_tier')}  conf={d.get('confidence'):.2f}")
        print(f"    why:    {d.get('evidence_reason')}")

    if args.write:
        case["media"] = [m.model_dump(mode="json") for m in media_canon]
        case["media_evidence"] = [m.model_dump(mode="json") for m in evidence_canon]
        args.canonical.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n  patched {args.canonical}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
