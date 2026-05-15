"""Fast 2025 candidate-list preview from Arab48's محليات (Local) listing.

Zero LLM cost. Walks the listing pages, parses titles + dates straight
from the HTML, filters to:
  * date in 2025-01-01 .. 2025-12-31
  * title contains at least one homicide keyword

Output:
  * Console: count + first 20 sample titles
  * CSV file at ``output/arab48_localities_2025_candidates.csv`` for review
    (columns: date, in_db, title, url)
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
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


# Homicide-context keywords. Verified against sample 2025 rollup titles:
#   - ``قتيل`` catches ``قتيلا`` (running-total rollups)
#   - ``قتل`` catches ``قُتلا`` / ``قُتل``
#   - ``مقتل`` catches articles about specific killings
#   - ``جريمة`` catches ``الجريمة المزدوجة`` and similar
# No need for the count-phrase variants — the existing set already hits
# every rollup title we've seen.
_KEYWORDS = [
    "قتل", "مقتل", "قتيل", "قُتل",
    "إطلاق نار", "رصاص",
    "طعن",
    "جريمة",
    "جثة", "نزيف", "حصيلة",
]


async def main() -> None:
    settings = Settings()
    init_db(settings.db_path)

    scraper = get_scraper("arab48", request_delay=0.1)
    print("Walking arab48/محليات for 2025-01-01..2025-12-31 ...")
    print(f"  Title keywords: {' / '.join(_KEYWORDS)}")
    print()

    candidates = await scraper.discover_from_category(
        category_path="/محليات",
        date_from="2025-01-01",
        date_to="2025-12-31",
        title_keywords=_KEYWORDS,
        max_results=2000,
        max_pages=160,
        listing_delay=0.2,
    )

    # Annotate with in-DB status so eyeballing is easy
    from crime_pipeline.models import RawArticle
    assert db_module.SessionLocal is not None
    with db_module.SessionLocal() as session:
        existing_urls = {
            r[0] for r in session.query(RawArticle.url).all()
        }

    in_db = sum(1 for d in candidates if d.url in existing_urls)
    new = len(candidates) - in_db
    print()
    print(f"=== {len(candidates)} candidates ({new} new, {in_db} already in DB) ===")
    print()

    # Group by month for quick eyeball
    from collections import Counter
    by_month: Counter = Counter()
    for d in candidates:
        if d.published_at:
            by_month[d.published_at.strftime("%Y-%m")] += 1
    print("Monthly distribution:")
    for m in sorted(by_month):
        bar = "█" * by_month[m]
        print(f"  {m}: {by_month[m]:3d}  {bar}")

    # Write CSV
    out_csv = Path("output/arab48_localities_2025_candidates.csv")
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
    print(f"  Open in Excel / VS Code to eyeball.")


if __name__ == "__main__":
    asyncio.run(main())
