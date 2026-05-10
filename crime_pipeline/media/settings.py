"""Media-subsystem configuration."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class MediaSettings(BaseModel):
    """Knobs governing image extraction, classification, and dedup."""

    enabled: bool = True
    max_images_per_article: int = 20
    max_images_per_case: int = 80
    max_image_size_mb: int = 5
    download_timeout_s: float = 10.0

    # Classification cascade controls
    enable_clip_classifier: bool = True
    enable_face_detection: bool = False
    max_vision_calls_per_case: int = 15
    keyword_confidence_threshold: float = 0.7
    clip_confidence_threshold: float = 0.25

    # Dedup thresholds
    phash_distance_threshold: int = 8
    clip_cosine_threshold: float = 0.92

    # Stock-photo demotion: only demote if confidence >= this; else flag for review
    stock_demotion_min_confidence: float = 0.7

    # Cache (URL hash → bytes; sha256 → metadata) for cross-source dedup speedup
    cache_dir: Path = Path(".cache/media")

    # Stock photo signal hosts
    stock_photo_domains: list[str] = Field(default_factory=lambda: [
        "gettyimages.", "shutterstock.", "istockphoto.", "depositphotos.",
        "alamy.", "stock.adobe.", "ap.org", "afp.com", "reuters.com/file",
    ])

    # Hebrew/Arabic illustration markers — caption containing these → likely stock
    stock_caption_markers: list[str] = Field(default_factory=lambda: [
        "אילוסטרציה", "ארכיון", "צילום אילוסטרציה",
        "للتوضيح", "أرشيف", "صورة من الأرشيف",
        "illustration", "file photo", "stock photo", "for illustration",
    ])
