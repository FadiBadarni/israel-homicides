"""Article media-extraction subsystem.

Public API:
    MediaPipeline          — per-case orchestrator (harvest → download → classify → dedup → split)
    MediaHarvester         — HTML → MediaCandidate
    MediaDownloader        — async image fetch + hashing
    MediaClassifier        — keyword/CLIP/Vision cascade
    ArticleContext         — case-level priors (victim/suspect/city names)
    MediaCandidate         — in-flight working object
    MediaSettings          — config block
    CanonicalMedia         — persisted record (re-exported from main models)
"""
from crime_pipeline.media.classifier import ArticleContext, MediaClassifier
from crime_pipeline.media.downloader import MediaDownloader
from crime_pipeline.media.harvester import MediaHarvester
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.pipeline import MediaPipeline
from crime_pipeline.media.settings import MediaSettings
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
