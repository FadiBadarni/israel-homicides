from .deduplicator import Deduplicator
from .embedder import ArticleEmbedder
from .graph import DeduplicationGraph
from .name_normalizer import (
    normalize_arabic,
    strip_honorifics,
    romanize_name,
    jaro_winkler_similarity,
)

__all__ = [
    "Deduplicator",
    "ArticleEmbedder",
    "DeduplicationGraph",
    "normalize_arabic",
    "strip_honorifics",
    "romanize_name",
    "jaro_winkler_similarity",
]
