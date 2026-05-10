"""Article media-extraction subsystem."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from crime_pipeline.media.classifier import ArticleContext, MediaClassifier
from crime_pipeline.media.downloader import MediaDownloader
from crime_pipeline.media.harvester import MediaHarvester
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings

if TYPE_CHECKING:
    from crime_pipeline.media.pipeline import MediaPipeline
    from crime_pipeline.models import CanonicalMedia

__all__ = [
    "MediaPipeline",
    "MediaHarvester",
    "MediaDownloader",
    "MediaClassifier",
    "ArticleContext",
    "MediaCandidate",
    "MediaSettings",
    "CanonicalMedia",
]


def __getattr__(name: str) -> Any:
    if name == "MediaPipeline":
        from crime_pipeline.media.pipeline import MediaPipeline

        return MediaPipeline
    if name == "CanonicalMedia":
        from crime_pipeline.models import CanonicalMedia

        return CanonicalMedia
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
