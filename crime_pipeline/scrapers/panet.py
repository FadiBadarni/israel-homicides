"""
Scraper for Panet.co.il — Arabic, JavaScript-rendered.

Uses Playwright (async_playwright) because Panet is a React/JS SPA.
A single shared Playwright browser is created on first use and reused
across calls via the class-level _browser / _playwright attributes.
Each fetch() gets its own BrowserContext (incognito) for isolation.

Rate limiting: asyncio.sleep(request_delay + random jitter 0-2 s) before
each navigation — applies inside both discover() and fetch().

Cloudflare detection: if page title contains "Just a moment" or the body
contains the CF challenge marker, fetch_status is set to "blocked".
"""

from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from .base import ArticleResult, BaseScraper, DiscoveredUrl

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.panet.co.il"
_SEARCH_URL_TPL = "https://www.panet.co.il/search?q={query}"

# Viewport that looks like a real desktop browser
_VIEWPORT = {"width": 1280, "height": 900}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Selectors tried in order when looking for article links on the search results page
_SEARCH_LINK_SELECTORS = [
    "a[data-testid='article-link']",
    "a.article-link",
    "div.search-result a",
    "div.news-item a",
    "li.search-item a",
    "article a",
    "h2 a",
    "h3 a",
]

# Selectors tried in order for article body on the article page
_ARTICLE_BODY_SELECTORS = [
    "[data-testid='article-body']",
    "div.article-content",
    "div.newsBody",
    "div.article-body",
    "div.body-content",
    "article .content",
    "div.post-content",
    "div.entry-content",
]

# Selectors for publication date on the article page
_DATE_SELECTORS = [
    "time[datetime]",
    "[data-testid='article-date']",
    "span.article-date",
    "div.article-date",
    "span.date",
    "div.date",
    "time",
]


def _parse_panet_date(raw: str) -> Optional[datetime]:
    """Try several date formats used by Panet."""
    raw = raw.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _is_cloudflare_challenge(title: str, content: str) -> bool:
    """Detect Cloudflare "Just a moment" challenge pages."""
    cf_signals = [
        "just a moment",
        "checking your browser",
        "please wait",
        "cf-browser-verification",
        "cf_chl_opt",
    ]
    combined = (title + " " + content).lower()
    return any(sig in combined for sig in cf_signals)


class PanetScraper(BaseScraper):
    source_name = "panet"
    language = "ar"
    base_domain = "www.panet.co.il"

    # Class-level shared browser / playwright handle
    _playwright: Optional[Playwright] = None
    _browser: Optional[Browser] = None
    _lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  Browser lifecycle                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    async def _ensure_browser(cls) -> Browser:
        """Launch the shared Playwright browser if not already running."""
        async with cls._lock:
            if cls._browser is None or not cls._browser.is_connected():
                if cls._playwright is None:
                    cls._playwright = await async_playwright().start()
                cls._browser = await cls._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                logger.debug("PanetScraper: Chromium browser launched")
        return cls._browser

    @classmethod
    async def close_browser(cls) -> None:
        """Gracefully close the shared browser (call at shutdown)."""
        async with cls._lock:
            if cls._browser and cls._browser.is_connected():
                await cls._browser.close()
                cls._browser = None
            if cls._playwright:
                await cls._playwright.stop()
                cls._playwright = None
            logger.debug("PanetScraper: browser closed")

    @asynccontextmanager
    async def _new_context(self) -> AsyncIterator[BrowserContext]:
        """Yield a fresh BrowserContext with realistic headers."""
        browser = await self._ensure_browser()
        ctx = await browser.new_context(
            viewport=_VIEWPORT,
            user_agent=_USER_AGENT,
            locale="ar-IL",
            timezone_id="Asia/Jerusalem",
            java_script_enabled=True,
            # Spoof navigator.webdriver = false
            extra_http_headers={"Accept-Language": "ar,he-IL;q=0.8,en-US;q=0.5"},
        )
        # Hide Playwright's webdriver flag
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        try:
            yield ctx
        finally:
            await ctx.close()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _sleep(self) -> None:
        delay = self.request_delay + random.uniform(0, 2)
        logger.debug("Panet: sleeping %.2f s", delay)
        await asyncio.sleep(delay)

    async def _navigate(
        self, page: Page, url: str, wait_until: str = "networkidle", timeout: int = 30_000
    ) -> bool:
        """Navigate to *url* and return True on success, False on timeout."""
        try:
            await self._sleep()
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return True
        except PlaywrightTimeout:
            logger.warning("Panet: navigation timeout for %s", url)
            return False

    # ------------------------------------------------------------------ #
    #  discover                                                            #
    # ------------------------------------------------------------------ #

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
    ) -> list[DiscoveredUrl]:
        """
        Search Panet.co.il for *query* using Playwright.
        Returns up to *max_results* DiscoveredUrl items.

        date_from / date_to: "YYYY-MM-DD" strings used for date filtering.
        """
        from_dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)

        search_url = _SEARCH_URL_TPL.format(query=quote_plus(query))
        discovered: list[DiscoveredUrl] = []

        async with self._new_context() as ctx:
            page = await ctx.new_page()
            try:
                ok = await self._navigate(page, search_url)
                if not ok:
                    logger.error("Panet discover: navigation timeout for %s", search_url)
                    return discovered

                # Check for Cloudflare challenge
                title = await page.title()
                body_text = await page.evaluate("document.body.innerText")
                if _is_cloudflare_challenge(title, body_text):
                    logger.warning("Panet discover: Cloudflare challenge detected")
                    return discovered

                # Try each link selector in order
                link_elements = []
                for sel in _SEARCH_LINK_SELECTORS:
                    link_elements = await page.query_selector_all(sel)
                    if link_elements:
                        logger.debug("Panet discover: matched selector %r (%d links)", sel, len(link_elements))
                        break

                # Fallback: all anchors with internal href
                if not link_elements:
                    link_elements = await page.query_selector_all("a[href]")

                seen: set[str] = set()
                for el in link_elements:
                    if len(discovered) >= max_results:
                        break

                    href = await el.get_attribute("href")
                    if not href:
                        continue
                    full_url = urljoin(_BASE_URL, href)
                    parsed = urlparse(full_url)
                    if parsed.netloc and self.base_domain not in parsed.netloc:
                        continue
                    # Skip non-article paths (home, category pages, etc.)
                    path = parsed.path
                    if not path or path == "/" or path.count("/") < 2:
                        continue
                    if full_url in seen:
                        continue
                    seen.add(full_url)

                    if not self.can_fetch(full_url):
                        logger.debug("Panet: robots.txt blocks %s", full_url)
                        continue

                    # Try to get date from the surrounding container via JS
                    pub_date: Optional[datetime] = None
                    try:
                        raw_date: Optional[str] = await el.evaluate(
                            """node => {
                                const container = node.closest('article, li, div.news-item, div.search-result');
                                if (!container) return null;
                                const dateEl = container.querySelector('time, [class*="date"], [data-date]');
                                if (!dateEl) return null;
                                return dateEl.getAttribute('datetime') || dateEl.getAttribute('data-date') || dateEl.innerText;
                            }"""
                        )
                        if raw_date:
                            pub_date = _parse_panet_date(raw_date)
                    except Exception:
                        pass

                    # Apply date filter when a date is available
                    if pub_date and not (from_dt <= pub_date <= to_dt):
                        continue

                    title_text: Optional[str] = None
                    try:
                        title_text = await el.inner_text()
                        title_text = title_text.strip() or None
                    except Exception:
                        pass

                    discovered.append(
                        DiscoveredUrl(
                            url=full_url,
                            source=self.source_name,
                            language=self.language,
                            title=title_text,
                            published_at=pub_date,
                            discovered_at=datetime.now(tz=timezone.utc),
                        )
                    )
            finally:
                await page.close()

        logger.info(
            "Panet discover: found %d URLs for query=%r", len(discovered), query
        )
        return discovered

    # ------------------------------------------------------------------ #
    #  fetch                                                               #
    # ------------------------------------------------------------------ #

    async def fetch(self, url: str) -> ArticleResult:
        """Fetch a single Panet article with Playwright; return ArticleResult."""
        if not self.can_fetch(url):
            return ArticleResult(
                url=url,
                final_url=url,
                source=self.source_name,
                language=self.language,
                title=None,
                published_at=None,
                raw_html="",
                article_text="",
                content_type="non_article",
                fetch_status="blocked",
                error_message="Blocked by robots.txt",
            )

        async with self._new_context() as ctx:
            page = await ctx.new_page()
            try:
                # Navigate; try networkidle first, fall back to domcontentloaded
                try:
                    await self._sleep()
                    await page.goto(url, wait_until="networkidle", timeout=30_000)
                except PlaywrightTimeout:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                    except PlaywrightTimeout:
                        return ArticleResult(
                            url=url,
                            final_url=url,
                            source=self.source_name,
                            language=self.language,
                            title=None,
                            published_at=None,
                            raw_html="",
                            article_text="",
                            content_type="non_article",
                            fetch_status="timeout",
                            error_message="Navigation timed out after 45 s",
                        )

                final_url = page.url
                page_title = await page.title()
                raw_html = await page.content()

                # --- Cloudflare check ---
                snippet = await page.evaluate("document.body.innerText.substring(0, 500)")
                if _is_cloudflare_challenge(page_title, snippet):
                    logger.warning("Panet fetch: Cloudflare challenge at %s", url)
                    return ArticleResult(
                        url=url,
                        final_url=final_url,
                        source=self.source_name,
                        language=self.language,
                        title=None,
                        published_at=None,
                        raw_html=raw_html,
                        article_text="",
                        content_type="non_article",
                        fetch_status="blocked",
                        error_message="Cloudflare challenge page",
                    )

                # --- Wait for article body ---
                article_el = None
                for sel in _ARTICLE_BODY_SELECTORS:
                    try:
                        article_el = await page.wait_for_selector(sel, timeout=5_000)
                        if article_el:
                            logger.debug("Panet fetch: body matched %r", sel)
                            break
                    except PlaywrightTimeout:
                        continue

                # --- Extract title ---
                title: Optional[str] = None
                try:
                    h1 = await page.query_selector("h1")
                    if h1:
                        title = (await h1.inner_text()).strip() or None
                except Exception:
                    pass

                # --- Extract body text ---
                article_text = ""
                if article_el:
                    try:
                        article_text = await article_el.evaluate(
                            "node => node.innerText"
                        )
                        article_text = article_text.strip()
                    except Exception:
                        pass

                # Fallback: use page.evaluate to gather all paragraph text
                if not article_text:
                    try:
                        article_text = await page.evaluate(
                            """() => {
                                const selectors = [
                                    '[data-testid="article-body"]',
                                    'div.article-content',
                                    'div.newsBody',
                                    'article'
                                ];
                                for (const sel of selectors) {
                                    const el = document.querySelector(sel);
                                    if (el) return el.innerText;
                                }
                                // Last resort: collect all <p> tags
                                return Array.from(document.querySelectorAll('p'))
                                    .map(p => p.innerText.trim())
                                    .filter(t => t.length > 20)
                                    .join('\\n\\n');
                            }"""
                        )
                        article_text = (article_text or "").strip()
                    except Exception:
                        article_text = ""

                # --- Extract publication date ---
                published_at: Optional[datetime] = None
                for sel in _DATE_SELECTORS:
                    try:
                        date_el = await page.query_selector(sel)
                        if not date_el:
                            continue
                        raw_date = await date_el.get_attribute("datetime")
                        if not raw_date:
                            raw_date = await date_el.inner_text()
                        if raw_date:
                            published_at = _parse_panet_date(raw_date.strip())
                            if published_at:
                                break
                    except Exception:
                        continue

                # --- Classify content ---
                content_type = self._classify_content(article_text, self.language)

                logger.info(
                    "Panet fetch: %s | title=%r | words=%d | type=%s",
                    final_url,
                    title,
                    len(article_text.split()),
                    content_type,
                )

                return ArticleResult(
                    url=url,
                    final_url=final_url,
                    source=self.source_name,
                    language=self.language,
                    title=title,
                    published_at=published_at,
                    raw_html=raw_html,
                    article_text=article_text,
                    content_type=content_type,
                    fetch_status="success",
                    error_message=None,
                )

            except Exception as exc:
                logger.exception("Panet fetch: unexpected error for %s", url)
                return ArticleResult(
                    url=url,
                    final_url=url,
                    source=self.source_name,
                    language=self.language,
                    title=None,
                    published_at=None,
                    raw_html="",
                    article_text="",
                    content_type="non_article",
                    fetch_status="fetch_failed",
                    error_message=str(exc),
                )
            finally:
                await page.close()
