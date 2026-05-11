from .base import BaseScraper, ArticleResult, DiscoveredUrl
from .arab48 import Arab48Scraper
from .israelhayom import IsraelhayomScraper
from .ynet import YnetScraper

SCRAPER_REGISTRY = {
    "ynet": YnetScraper,
    "arab48": Arab48Scraper,
    "israelhayom": IsraelhayomScraper,
}


def get_scraper(source: str, **kwargs) -> BaseScraper:
    if source not in SCRAPER_REGISTRY:
        raise ValueError(
            f"Unknown source: {source}. Available: {list(SCRAPER_REGISTRY.keys())}"
        )
    return SCRAPER_REGISTRY[source](**kwargs)
