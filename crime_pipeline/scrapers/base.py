from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ArticleResult:
    url: str
    final_url: str
    source: str  # "panet" | "ynet"
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
        """Check robots.txt. Returns True if allowed or respect_robots=False."""
        if not self.respect_robots:
            return True
        from urllib.parse import urlparse
        from urllib.robotparser import RobotFileParser

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        if robots_url not in self._robots_cache:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                rp.read()
                self._robots_cache[robots_url] = rp
            except Exception:
                # If we can't read robots.txt, be permissive
                return True
        return self._robots_cache[robots_url].can_fetch("*", url)

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
