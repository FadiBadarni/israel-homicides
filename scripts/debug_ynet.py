"""Quick debug runner for the Ynet scraper - discover + fetch, no DB."""
import asyncio
import io
import logging
import sys
import textwrap

# Force UTF-8 output on Windows so Hebrew/Arabic text prints correctly
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from crime_pipeline.scrapers.ynet import YnetScraper

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-7s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

QUERY = "ירי רצח"  # ירי רצח (shooting / murder)
DATE_FROM = "2026-01-01"
DATE_TO   = "2026-12-31"
MAX_DISCOVER = 10
MAX_FETCH    = 3


async def main() -> None:
    scraper = YnetScraper(request_delay=1.0, respect_robots=True)

    print("\n" + "=" * 60)
    print(f"DISCOVER  query={QUERY!r}  {DATE_FROM} to {DATE_TO}")
    print("=" * 60)

    discovered = await scraper.discover(QUERY, DATE_FROM, DATE_TO, max_results=MAX_DISCOVER)

    if not discovered:
        print("!! discover() returned 0 results - check search URL / selectors")
        return

    print(f"\nFound {len(discovered)} URL(s):\n")
    for i, d in enumerate(discovered, 1):
        print(f"  [{i}] {d.url}")
        print(f"       title  : {d.title!r}")
        print(f"       pub_at : {d.published_at}")
        print()

    print("\n" + "=" * 60)
    print(f"FETCH  (first {min(MAX_FETCH, len(discovered))} articles)")
    print("=" * 60 + "\n")

    for d in discovered[:MAX_FETCH]:
        result = await scraper.fetch(d.url)
        print(f"URL    : {result.final_url}")
        print(f"status : {result.fetch_status}   type={result.content_type}")
        print(f"title  : {result.title!r}")
        print(f"pub_at : {result.published_at}")
        print(f"words  : {len(result.article_text.split())}")
        if result.error_message:
            print(f"error  : {result.error_message}")
        if result.article_text:
            preview = textwrap.shorten(result.article_text, width=300, placeholder="...")
            print(f"body   : {preview}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
