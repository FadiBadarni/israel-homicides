from .base import BaseScraper, ArticleResult, DiscoveredUrl
from .arab48 import Arab48Scraper
from .israelhayom import IsraelhayomScraper
from .kul_alarab import KulAlarabScraper
from .makan import MakanScraper
from .panet import PanetScraper
from .walla import WallaScraper
from .ynet import YnetScraper

SCRAPER_REGISTRY = {
    "ynet": YnetScraper,
    "arab48": Arab48Scraper,
    "israelhayom": IsraelhayomScraper,
    "kul_alarab": KulAlarabScraper,
    "makan": MakanScraper,
    "panet": PanetScraper,
    "walla": WallaScraper,
}


def get_scraper(source: str, **kwargs) -> BaseScraper:
    if source not in SCRAPER_REGISTRY:
        raise ValueError(
            f"Unknown source: {source}. Available: {list(SCRAPER_REGISTRY.keys())}"
        )
    return SCRAPER_REGISTRY[source](**kwargs)
