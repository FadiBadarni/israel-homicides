from .base import BaseScraper, ArticleResult, DiscoveredUrl
from .ynet import YnetScraper
from .panet import PanetScraper

SCRAPER_REGISTRY = {
    "ynet": YnetScraper,
    "panet": PanetScraper,
}


def get_scraper(source: str, **kwargs) -> BaseScraper:
    if source not in SCRAPER_REGISTRY:
        raise ValueError(
            f"Unknown source: {source}. Available: {list(SCRAPER_REGISTRY.keys())}"
        )
    return SCRAPER_REGISTRY[source](**kwargs)
