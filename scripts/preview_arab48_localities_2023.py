"""Fast 2023 candidate-list preview from arab48/محليات.

Identical recipe to 2024 / 2025 previews, only the date window changes.
Walks listing pages, filters by date + homicide-keyword title, writes CSV.

Output: output/arab48_localities_2023_candidates.csv  (no LLM cost).
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.scrapers import get_scraper
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


_KEYWORDS = [
    "قتل", "مقتل", "قتيل", "قُتل",
    "إطلاق نار", "رصاص",
    "طعن",
    "جريمة",
    "جثة", "نزيف", "حصيلة",
]

DATE_FROM = "2023-01-01"
DATE_TO = "2023-12-31"
# Pages 200-280 cover 2023 (verified earlier). Cap at 300 for headroom.
MAX_PAGES = 300


async def main() -> None:
    settings = Settings()
    init_db(settings.db_path)

    scraper = get_scraper("arab48", request_delay=0.1)
    print(f"Walking arab48/محليات for {DATE_FROM}..{DATE_TO} ...")
    print(f"  Title keywords: {' / '.join(_KEYWORDS)}")
    print()

    candidates = await scraper.discover_from_category(
        category_path="/محليات",
        date_from=DATE_FROM,
        date_to=DATE_TO,
        title_keywords=_KEYWORDS,
        max_results=2000,
        max_pages=MAX_PAGES,
        listing_delay=0.2,
    )

    from crime_pipeline.models import RawArticle
    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        existing_urls = {r[0] for r in session.query(RawArticle.url).all()}

    in_db = sum(1 for d in candidates if d.url in existing_urls)
    new = len(candidates) - in_db
    print()
    print(f"=== {len(candidates)} candidates ({new} new, {in_db} already in DB) ===")
    print()

    by_month: Counter = Counter()
    for d in candidates:
        if d.published_at:
            by_month[d.published_at.strftime("%Y-%m")] += 1
    print("Monthly distribution:")
    for m in sorted(by_month):
        bar = "█" * by_month[m]
        print(f"  {m}: {by_month[m]:3d}  {bar}")

    out_csv = Path("output/arab48_localities_2023_candidates.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "in_db", "title", "url"])
        for d in sorted(
            candidates,
            key=lambda x: x.published_at.isoformat() if x.published_at else "",
            reverse=True,
        ):
            w.writerow([
                d.published_at.date().isoformat() if d.published_at else "",
                "yes" if d.url in existing_urls else "no",
                d.title or "",
                d.url,
            ])
    print()
    print(f"Wrote candidate list to: {out_csv}")


if __name__ == "__main__":
    asyncio.run(main())
