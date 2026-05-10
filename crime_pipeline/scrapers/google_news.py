"""
Google News RSS-based discoverer.

Uses Google News' public RSS feed to discover articles matching a query.
This bypasses brittle site-specific search URLs and returns articles from
many Israeli sources at once (Ynet, Mako, Walla, Haaretz, Times of Israel,
Panet, Bokra, etc.) in one call.

The fetch step is delegated to a generic httpx-based fetcher that works for
most server-rendered news sites. Cloudflare-protected or JS-only sites will
return content_type=non_article, which the pipeline handles gracefully.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

from .base import ArticleResult, BaseScraper, DiscoveredUrl

log = structlog.get_logger()


def _detect_language(url: str, title: str) -> str:
    """Heuristic language detection based on URL/title characters."""
    text = (url + " " + (title or "")).lower()
    # Arabic block U+0600–U+06FF
    if re.search(r"[؀-ۿ]", text):
        return "ar"
    # Hebrew block U+0590–U+05FF
    if re.search(r"[֐-׿]", text):
        return "he"
    # Domain-based fallback
    if any(d in url for d in ("panet.co.il", "bokra.net", "arab48.com", "alarab.com")):
        return "ar"
    if any(d in url for d in ("ynet.co.il", "mako.co.il", "walla.co.il", "haaretz.co.il",
                              "kan.org.il", "n12.co.il", "maariv.co.il", "globes.co.il",
                              "police.gov.il", "inn.co.il", "calcalist.co.il")):
        return "he"
    return "he"  # default for Israel-focused queries


class GoogleNewsScraper(BaseScraper):
    """
    Discovery via Google News RSS; fetch via generic httpx + BeautifulSoup.

    Google News RSS endpoint:
        https://news.google.com/rss/search?q=<query>&hl=he&gl=IL&ceid=IL:he
    """

    source_name = "googlenews"
    language = "he"
    base_domain = "news.google.com"

    # Domain → tier mapping for source priority
    SOURCE_TIER = {
        "police.gov.il": "police",
        "ynet.co.il": "ynet",
        "panet.co.il": "panet",
        "bokra.net": "panet",
        "arab48.com": "panet",
        "alarab.com": "panet",
    }

    # Locale presets — switches the Google News result language/region.
    # Arabic locale surfaces panet.co.il, bokra.net, alarab.com etc;
    # Hebrew (default) surfaces ynet, haaretz, mako, kan, channel 13 etc.
    LOCALES = {
        "he": "hl=iw&gl=IL&ceid=IL:iw",
        "ar": "hl=ar&gl=IL&ceid=IL:ar",
        "en": "hl=en-IL&gl=IL&ceid=IL:en",
    }

    def __init__(
        self,
        request_delay: float = 3.0,
        respect_robots: bool = True,
        locale: str = "he",
    ) -> None:
        super().__init__(request_delay=request_delay, respect_robots=respect_robots)
        self._timeout = httpx.Timeout(30.0)
        self.locale = locale if locale in self.LOCALES else "he"
        self.language = self.locale

    async def discover(
        self,
        query: str,
        date_from: str,
        date_to: str,
        max_results: int = 50,
    ) -> list[DiscoveredUrl]:
        """Search Google News RSS for the given query."""
        locale_qs = self.LOCALES[self.locale]
        rss_url = (
            f"https://news.google.com/rss/search?q={quote_plus(query)}&{locale_qs}"
        )
        log.info("googlenews_discover", query=query, rss_url=rss_url)

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CrimePipeline/0.1)"},
            ) as client:
                resp = await client.get(rss_url)
                resp.raise_for_status()
                xml_text = resp.text
        except Exception as e:
            log.error("googlenews_rss_error", error=str(e))
            return []

        # Parse RSS via BeautifulSoup — simpler than feedparser for our needs
        soup = BeautifulSoup(xml_text, "xml")
        items = soup.find_all("item")

        date_from_dt = self._parse_date(date_from)
        date_to_dt = self._parse_date(date_to)

        out: list[DiscoveredUrl] = []
        seen_urls: set[str] = set()

        for item in items:
            link_el = item.find("link")
            title_el = item.find("title")
            pub_el = item.find("pubDate")
            desc_el = item.find("description")
            source_el = item.find("source")
            if not link_el:
                continue
            url = (link_el.text or "").strip()
            title = (title_el.text or "").strip() if title_el else None

            published = None
            if pub_el and pub_el.text:
                try:
                    published = parsedate_to_datetime(pub_el.text.strip())
                except Exception:
                    published = None

            # Filter by date range
            if published and date_from_dt and date_to_dt:
                if published < date_from_dt or published > date_to_dt:
                    continue

            # Strategy 1: Try description anchor that's not a google news link
            real_url: str | None = None
            if desc_el and desc_el.text:
                desc_soup = BeautifulSoup(desc_el.text, "lxml")
                anchors = desc_soup.find_all("a", href=True)
                for a in anchors:
                    href = a["href"]
                    if "news.google.com" not in href and href.startswith("http"):
                        real_url = href
                        break

            # Strategy 2: Use googlenewsdecoder to decode the encrypted URL.
            if not real_url and "news.google.com/rss/articles" in url:
                try:
                    from googlenewsdecoder import gnewsdecoder
                    decoded = gnewsdecoder(url, interval=1)
                    if decoded.get("status") and decoded.get("decoded_url"):
                        real_url = decoded["decoded_url"]
                except Exception as e:
                    log.debug("gnewsdecoder_error", error=str(e), url=url[:80])

            # Strategy 3: Last-ditch fallback — keep the news.google.com URL.
            if not real_url:
                real_url = url

            if not real_url or real_url in seen_urls:
                continue
            seen_urls.add(real_url)

            out.append(
                DiscoveredUrl(
                    url=real_url,
                    source=self.source_name,
                    language=_detect_language(real_url, title),
                    title=title,
                    published_at=published,
                    discovered_at=datetime.now(timezone.utc),
                )
            )
            if len(out) >= max_results:
                break

        log.info("googlenews_discover_done", count=len(out), query=query)
        return out

    async def _resolve_redirect(self, gn_url: str) -> str | None:
        """Follow the google.com redirect to find the real article URL."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CrimePipeline/0.1)"},
            ) as client:
                resp = await client.head(gn_url)
                final = str(resp.url)
                # Google News sometimes responds with HTML instead of redirect.
                # Fall back to GET and parse the URL out.
                if "news.google.com" in final:
                    resp = await client.get(gn_url)
                    # Look for canonical URL in HTML
                    m = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*data-n-au', resp.text)
                    if m:
                        return m.group(1)
                    m = re.search(r'data-n-au="(https?://[^"]+)"', resp.text)
                    if m:
                        return m.group(1)
                    return None
                return final
        except Exception:
            return None

    async def fetch(self, url: str) -> ArticleResult:
        """Generic article fetcher — works for most server-rendered news sites."""
        await asyncio.sleep(self.request_delay + random.uniform(0, 1.5))

        if not self.can_fetch(url):
            return ArticleResult(
                url=url, final_url=url, source=self._infer_source_from_url(url),
                language=_detect_language(url, ""),
                title=None, published_at=None,
                raw_html="", article_text="",
                content_type="non_article", fetch_status="blocked",
                error_message="robots.txt disallows",
            )

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "he-IL,he;q=0.9,ar;q=0.8,en;q=0.7",
                },
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                final_url = str(resp.url)
                html = resp.text
        except Exception as e:
            return ArticleResult(
                url=url, final_url=url, source=self._infer_source_from_url(url),
                language=_detect_language(url, ""),
                title=None, published_at=None,
                raw_html="", article_text="",
                content_type="non_article", fetch_status="fetch_failed",
                error_message=str(e),
            )

        # Try trafilatura first — handles many sites well
        try:
            import trafilatura
            text = trafilatura.extract(html, include_comments=False, include_tables=False)
        except Exception:
            text = None

        # Fallback: BeautifulSoup paragraph extraction
        if not text or len(text.split()) < 30:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "nav", "aside", "footer", "header"]):
                tag.decompose()
            paragraphs = soup.find_all("p")
            text = "\n\n".join(
                p.get_text(separator=" ", strip=True)
                for p in paragraphs
                if len(p.get_text(strip=True)) > 30
            )

        # Title extraction
        title = None
        soup_title = BeautifulSoup(html, "lxml")
        if soup_title.title:
            title = soup_title.title.get_text(strip=True)
        h1 = soup_title.find("h1")
        if h1:
            title = h1.get_text(strip=True) or title

        language = _detect_language(final_url, title or "")
        source = self._infer_source_from_url(final_url)

        if not text or len(text.split()) < 50:
            return ArticleResult(
                url=url, final_url=final_url, source=source, language=language,
                title=title, published_at=None,
                raw_html=html, article_text=text or "",
                content_type="non_article", fetch_status="success",
                error_message="article body too short",
            )

        return ArticleResult(
            url=url, final_url=final_url, source=source, language=language,
            title=title, published_at=None,
            raw_html=html, article_text=text,
            content_type="article", fetch_status="success",
        )

    def _infer_source_from_url(self, url: str) -> str:
        try:
            host = urlparse(url).netloc.lower().replace("www.", "")
        except Exception:
            return self.source_name
        for domain, tier in self.SOURCE_TIER.items():
            if domain in host:
                return tier
        return self.source_name

    @staticmethod
    def _parse_date(s: str) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None
