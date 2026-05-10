"""Fetch 3 real Israeli-news articles and run the media pipeline.

Demonstrates the media subsystem against actual publisher HTML — Channel 13,
Mako, Ynet — so we can see whether cross-publisher portrait dedup and the
new corroboration check produce useful evidence routing.

Run:
    python scripts/demo_media_real.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# Ensure the project root is importable when running from scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from crime_pipeline.media import (
    ArticleContext, MediaPipeline, MediaSettings,
)


URLS = [
    "https://13tv.co.il/item/news/domestic/crime-and-justice/arraba-904915890/",
    "https://www.mako.co.il/news-law/2026_q1/Article-885d90ca2ca2c91026.htm",
    "https://www.ynet.co.il/news/article/h165rozp11e",
]

# Case context — the Arraba 2026-01-03 homicide.
VICTIM_NAMES = ["בכר יאסין", "Bakr Yassin", "بكر ياسين"]
SUSPECT_NAMES: list[str] = []  # not yet known publicly
CITY_NAMES = ["עראבה", "Arraba", "عرابة"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


async def fetch_all(urls: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        http2=True, follow_redirects=True, timeout=30.0,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "he,en;q=0.7,ar;q=0.5"},
    ) as client:
        for u in urls:
            try:
                r = await client.get(u)
                if r.status_code != 200:
                    print(f"  !! {u} -> HTTP {r.status_code}")
                    continue
                out.append({"raw_html": r.text, "url": str(r.url)})
                print(f"  ok {u} -> {r.status_code} ({len(r.text):,} bytes)")
            except Exception as e:
                print(f"  !! {u} -> error: {e}")
    return out


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    print("Fetching publisher pages...")
    articles = await fetch_all(URLS)
    print()

    if not articles:
        print("No articles fetched, aborting.")
        return 2

    ctx = ArticleContext(
        article_url=articles[0]["url"],
        victim_names=VICTIM_NAMES,
        suspect_names=SUSPECT_NAMES,
        city_names=CITY_NAMES,
    )
    pipe = MediaPipeline(MediaSettings())
    media, evidence = await pipe.run_for_case(articles, ctx)

    print(f"=== Result: {len(evidence)} evidence + {len(media)} decorative ===\n")

    def show(items, label: str) -> None:
        if not items:
            print(f"--- {label}: (empty) ---\n")
            return
        print(f"--- {label} ({len(items)}) ---")
        for m in items:
            print(f"[{m.type:18s}] conf={m.confidence:.2f}  appear={m.appearance_count}  "
                  f"stock={m.is_stock_photo}")
            print(f"    primary_url: {m.primary_url[:110]}")
            if m.mirror_urls:
                print(f"    mirrors:     {len(m.mirror_urls)} hosts")
                for mu in m.mirror_urls[:3]:
                    print(f"                 - {mu[:110]}")
            if m.caption:
                print(f"    caption:     {m.caption[:90]}")
            if m.alt_text and m.alt_text != m.caption:
                print(f"    alt:         {m.alt_text[:90]}")
            print(f"    sources:     {len(m.source_article_urls)} article(s)")
            print(f"    evidence:    {m.classification_evidence}")
            print(f"    reason:      {m.evidence_reason}")
            print()

    show(evidence, "media_evidence")
    show(media, "media (decorative)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
