from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ArticleResult:
    url: str
    final_url: str
    source: str  # "arab48" | "ynet"
    language: str  # "ar" | "he"
    title: Optional[str]
    published_at: Optional[datetime]
    raw_html: str
    article_text: str
    content_type: str  # "article" | "partial" | "non_article"
    fetch_status: str  # "success" | "fetch_failed" | "blocked" | "timeout"
    error_message: Optional[str] = None


@dataclass
class DiscoveredUrl:
    url: str
    source: str
    language: str
    title: Optional[str]
    published_at: Optional[datetime]
    discovered_at: datetime


class BaseScraper(ABC):
    source_name: str
    language: str
    base_domain: str

    def __init__(self, request_delay: float = 3.0, respect_robots: bool = True):
        self.request_delay = request_delay
        self.respect_robots = respect_robots
        self._robots_cache: dict = {}

    @abstractmethod
    async def discover(
        self, query: str, date_from: str, date_to: str, max_results: int = 50
    ) -> list[DiscoveredUrl]:
        """Search source for articles matching query in date range."""
        pass

    @abstractmethod
    async def fetch(self, url: str) -> ArticleResult:
        """Fetch and extract clean text from a single article URL."""
        pass

    def can_fetch(self, url: str) -> bool:
        """Check robots.txt. Returns True if allowed or respect_robots=False.

        Fetches the robots.txt via ``httpx`` (with a real browser User-Agent)
        rather than ``urllib.robotparser.RobotFileParser.read()``, because
        some hosts (Cloudflare-fronted sites like Makan/Kan) return 403 or
        an empty body to urllib's default UA. When ``rp.read()`` fails
        silently, the parser is left with no default_entry and ALL
        ``can_fetch`` calls return False — silently blocking the scraper
        from every URL.
        """
        if not self.respect_robots:
            return True
        from urllib.parse import urlparse
        from urllib.robotparser import RobotFileParser

        import httpx

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        if robots_url not in self._robots_cache:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            body: str | None = None
            try:
                resp = httpx.get(
                    robots_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                    },
                    timeout=10.0,
                    follow_redirects=True,
                )
                if resp.status_code == 200:
                    body = resp.text
            except httpx.HTTPError:
                body = None

            if body is not None:
                rp.parse(body.splitlines())
            else:
                # No robots.txt or unreachable — fall back to permissive.
                # Mark as a sentinel so we don't retry every URL.
                rp = None  # type: ignore[assignment]
            self._robots_cache[robots_url] = rp
        cached = self._robots_cache[robots_url]
        if cached is None:
            return True
        return cached.can_fetch("*", url)

    def _extract_clean_text(
        self, html: str, title_selector: str, body_selectors: list[str]
    ) -> tuple[str, str]:
        """Extract title and body text using CSS selectors. Returns (title, body)."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        title = ""
        title_el = soup.select_one(title_selector)
        if title_el:
            title = title_el.get_text(strip=True)
        body_parts = []
        for sel in body_selectors:
            elements = soup.select(sel)
            for el in elements:
                text = el.get_text(separator=" ", strip=True)
                if text:
                    body_parts.append(text)
        return title, "\n\n".join(body_parts)

    def _is_non_article(self, text: str) -> bool:
        """True when text has fewer than 50 words — likely not a real article."""
        return len(text.split()) < 50

    def _is_partial(self, text: str, language: str) -> bool:
        """True when paywall / subscription signals are found in the text."""
        subscription_signals: dict[str, list[str]] = {
            "he": ["להמשך קריאה", "לכתבה המלאה", "הירשם לקרוא", "מנויים בלבד"],
            "ar": ["اشترك الآن", "للمزيد اشترك", "اقرأ المزيد", "المشتركون فقط"],
        }
        signals = subscription_signals.get(language, [])
        return any(signal in text for signal in signals)

    def _classify_content(self, text: str, language: str) -> str:
        """Return 'non_article', 'partial', or 'article'."""
        if self._is_non_article(text):
            return "non_article"
        if self._is_partial(text, language):
            return "partial"
        return "article"
