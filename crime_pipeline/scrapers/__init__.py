from .base import BaseScraper, ArticleResult, DiscoveredUrl
from .ynet import YnetScraper
from .police import PoliceScraper
from .panet import PanetScraper
from .google_news import GoogleNewsScraper

SCRAPER_REGISTRY = {
    "ynet": YnetScraper,
    "police": PoliceScraper,
    "panet": PanetScraper,
    "googlenews": GoogleNewsScraper,
}


def get_scraper(source: str, **kwargs) -> BaseScraper:
    if source not in SCRAPER_REGISTRY:
        raise ValueError(
            f"Unknown source: {source}. Available: {list(SCRAPER_REGISTRY.keys())}"
        )
    return SCRAPER_REGISTRY[source](**kwargs)
