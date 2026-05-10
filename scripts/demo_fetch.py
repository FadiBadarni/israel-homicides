"""Quick fetch demo — no DB, no LLM, no export.

Runs discover + fetch for a query against ynet and panet,
prints what each fetched article contains.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crime_pipeline.scrapers import get_scraper
from crime_pipeline.scrapers.panet import PanetScraper

QUERY = "Arraba 2026 Bakr Yassin"
DATE_FROM = "2026-01-01"
DATE_TO = "2026-12-31"
MAX_DISCOVER = 5   # URLs to discover per source
MAX_FETCH = 3      # Articles to actually fetch per source


def _bar(label: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")


def _show(result) -> None:
    print(f"  url          : {result.final_url}")
    print(f"  source       : {result.source}")
    print(f"  language     : {result.language}")
    print(f"  status       : {result.fetch_status}")
    print(f"  content_type : {result.content_type}")
    print(f"  title        : {result.title!r}")
    print(f"  published_at : {result.published_at}")
    print(f"  word_count   : {len((result.article_text or '').split())}")
    print(f"  html_bytes   : {len(result.raw_html.encode())}")
    if result.error_message:
        print(f"  error        : {result.error_message}")
    if result.article_text:
        preview = result.article_text[:400].replace("\n", " ")
        print(f"  text_preview : {preview}…")
    print()


async def main() -> None:
    for source in ("ynet", "panet"):
        _bar(f"SOURCE: {source.upper()}  —  discover")
        scraper = get_scraper(source, request_delay=1.0, respect_robots=True)

        urls = await scraper.discover(QUERY, DATE_FROM, DATE_TO, max_results=MAX_DISCOVER)
        print(f"  Discovered {len(urls)} URL(s):")
        for u in urls:
            print(f"    [{u.language}] {u.published_at or 'no-date'}  {u.url}")
            if u.title:
                print(f"           title: {u.title!r}")

        if not urls:
            print("  → nothing discovered, skipping fetch")
            continue

        _bar(f"SOURCE: {source.upper()}  —  fetch (first {MAX_FETCH})")
        for du in urls[:MAX_FETCH]:
            print(f"\n  Fetching: {du.url}")
            result = await scraper.fetch(du.url)
            _show(result)

    await PanetScraper.close_browser()


if __name__ == "__main__":
    asyncio.run(main())
