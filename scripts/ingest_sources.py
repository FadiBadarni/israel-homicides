"""
Ingest the three publisher URLs (Channel 13, Mako, Ynet) directly into
the pipeline DB and run LLM extraction on them.

Usage:
    python scripts/ingest_sources.py

After this script completes, run:
    python -m crime_pipeline --query "Arraba 2026" \
        --stage dedup --stage merge --stage export --run-id arraba_full_001
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the project importable from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

from crime_pipeline.config import Settings
from crime_pipeline.extraction.extractor import ArticleExtractor
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db
from crime_pipeline.storage.repository import (
    get_articles_by_status,
    save_article,
    save_extraction,
)

TARGET_URLS = [
    {
        "url": "https://13tv.co.il/item/news/domestic/crime-and-justice/arraba-904915890/",
        "source": "channel13",
        "language": "he",
    },
    {
        "url": "https://www.mako.co.il/news-law/2026_q1/Article-885d90ca2ca2c91026.htm",
        "source": "mako",
        "language": "he",
    },
    {
        "url": "https://www.ynet.co.il/news/article/h165rozp11e",
        "source": "ynet",
        "language": "he",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def _extract_article_text(html: str, url: str) -> str:
    """Extract readable article text from raw HTML."""
    soup = BeautifulSoup(html, "lxml")

    # Remove noise
    for tag in soup.find_all(["script", "style", "nav", "header", "footer",
                               "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    # Try article-specific selectors first
    selectors = [
        "article",
        '[data-testid="article-body"]',
        ".article-body",
        ".art-body",
        ".article_body",
        ".story-body",
        ".content-body",
        "main",
        ".content",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            paras = el.find_all("p")
            if paras:
                return " ".join(p.get_text(" ", strip=True) for p in paras)
            text = el.get_text(" ", strip=True)
            if len(text) > 200:
                return text

    # Fallback: all <p> tags
    paras = soup.find_all("p")
    text = " ".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 30)
    return text


def _extract_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for sel in ["h1.mainTitle", "h1.art-title", "h1[data-testid='article-title']", "h1"]:
        el = soup.select_one(sel)
        if el:
            return el.get_text(strip=True)
    meta = soup.find("meta", property="og:title") or soup.find("meta", {"name": "title"})
    if meta and meta.get("content"):
        return meta["content"]
    if soup.title:
        return soup.title.get_text(strip=True)
    return None


async def fetch_and_save(client: httpx.AsyncClient, target: dict) -> tuple[str, bool]:
    """Fetch URL, upsert into raw_articles. Returns (article_id, is_new)."""
    url = target["url"]
    source = target["source"]
    language = target["language"]

    settings = Settings()  # type: ignore[call-arg]
    init_db(str(settings.db_path))

    # Check if already in DB
    with db_module.SessionLocal() as session:
        from sqlalchemy import select
        from crime_pipeline.models import RawArticle
        existing = session.scalar(select(RawArticle).where(RawArticle.url == url))
        if existing and existing.article_text:
            print(f"  [SKIP] Already in DB: {url[:60]}")
            return existing.id, False

    print(f"  [FETCH] {url[:70]}")
    try:
        resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"  [ERROR] HTTP error for {url}: {e}")
        return "", False

    html = resp.text
    article_text = _extract_article_text(html, url)
    title = _extract_title(html)

    print(f"  [OK] {url[:60]} — text={len(article_text)} chars, title={repr(title[:50]) if title else None}")

    with db_module.SessionLocal() as session:
        article = save_article(session, {
            "source": source,
            "url": url,
            "final_url": str(resp.url),
            "language": language,
            "title": title,
            "published_at": None,
            "raw_html": html,
            "article_text": article_text,
            "content_type": "article",
            "fetch_status": "success",
            "fetched_at": datetime.now(tz=timezone.utc),
        })
        session.commit()
        return article.id, True


async def extract_articles(article_ids: list[str], settings: Settings) -> None:
    """Run LLM extraction on specific article IDs and persist to DB."""
    with db_module.SessionLocal() as session:
        articles = get_articles_by_status(session, "success")
        target_articles = [a for a in articles if a.id in set(article_ids) and a.article_text]

    if not target_articles:
        print("[WARN] No extractable articles found")
        return

    print(f"\nExtracting {len(target_articles)} articles via Gemini...")

    extractor = ArticleExtractor(
        api_key=settings.gemini_api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
        concurrency=3,
    )

    article_inputs = [
        {
            "article_id": a.id,
            "article_text": a.article_text,
            "language": a.language,
            "source": a.source,
            "published_at": a.published_at.isoformat() if a.published_at else None,
        }
        for a in target_articles
    ]

    results = await extractor.extract_batch(article_inputs)

    with db_module.SessionLocal() as session:
        for inp, res in zip(article_inputs, results):
            if res.get("status") == "success" and res.get("extracted_data"):
                save_extraction(session, inp["article_id"], {
                    "extracted_json": res["extracted_data"],
                    "validation_status": "valid",
                    "llm_model": settings.llm_model,
                    "input_tokens": res.get("input_tokens", 0),
                    "output_tokens": res.get("output_tokens", 0),
                    "cache_hit": res.get("cache_hit", False),
                    "latency_ms": res.get("latency_ms", 0),
                    "extraction_status": "success",
                })
                print(f"  [EXTRACTED] {inp['article_id'][:8]} — {res['extracted_data'].get('victim_name', '?')}")
            else:
                print(f"  [FAILED] {inp['article_id'][:8]} — {res.get('error', 'unknown')}")
        session.commit()


async def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set")
        sys.exit(1)

    settings = Settings()  # type: ignore[call-arg]
    init_db(str(settings.db_path))

    print("=== Ingesting source URLs ===")
    article_ids: list[str] = []
    async with httpx.AsyncClient() as client:
        for target in TARGET_URLS:
            aid, _ = await fetch_and_save(client, target)
            if aid:
                article_ids.append(aid)

    print(f"\nArticle IDs to extract: {[i[:8] for i in article_ids]}")

    # Filter out IDs that already have an extraction record
    from sqlalchemy import select
    from crime_pipeline.models import ExtractedRecord
    with db_module.SessionLocal() as session:
        already_extracted = {
            r.article_id for r in session.scalars(
                select(ExtractedRecord).where(
                    ExtractedRecord.article_id.in_(article_ids)
                )
            )
        }
    ids_to_extract = [i for i in article_ids if i not in already_extracted]
    if already_extracted:
        print(f"  [SKIP] Already extracted: {[i[:8] for i in already_extracted]}")
    if not ids_to_extract:
        print("All articles already extracted. Running dedup/merge/export only.")
        return

    await extract_articles(ids_to_extract, settings)
    print("\nDone. Now run:")
    print("  python -m crime_pipeline --query \"Arraba 2026\" --stage dedup --stage merge --stage export")


if __name__ == "__main__":
    asyncio.run(main())
