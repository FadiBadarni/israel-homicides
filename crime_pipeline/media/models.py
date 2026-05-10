"""In-flight working object for the media pipeline.

`MediaCandidate` is the per-image working state that flows through harvest →
download → hash → classify. It is NOT directly persisted in the canonical
case JSON. The persisted shape is `crime_pipeline.models.CanonicalMedia`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


DownloadStatus = Literal[
    "pending", "ok", "timeout", "http_error", "too_large",
    "unsupported_format", "skipped", "blocked",
]

MediaCategory = Literal[
    "victim_portrait", "suspect_portrait", "crime_scene", "weapon",
    "court", "funeral", "cctv", "police_activity", "generic_stock",
    "infographic", "video", "other",
]


class MediaCandidate(BaseModel):
    """In-flight working object — never persisted directly."""

    # Identity
    source_article_url: str
    source_url: str  # the image URL as found in HTML
    final_url: Optional[str] = None  # post-redirect
    discovery_selector: str  # which extraction rule found this

    # Raw HTML signals
    caption: Optional[str] = None
    alt_text: Optional[str] = None
    figcaption: Optional[str] = None
    surrounding_text: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    # Computed (after download)
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    phash: Optional[str] = None  # 16-hex-char string (64-bit)
    clip_embedding: Optional[list[float]] = None
    face_count: Optional[int] = None
    bytes_ref: Optional[str] = None  # cache file path; raw bytes never serialized

    # Classification
    classification: Optional[MediaCategory] = None
    classifier_tier: Optional[Literal["keyword", "clip", "gemini", "manual"]] = None
    classification_confidence: float = 0.0
    classification_evidence: list[str] = Field(
        default_factory=list,
        description="Signals that fired during classification, e.g. "
                    "['caption_match:victim_name', 'clip:0.31', 'phash_match:5']",
    )
    is_stock_photo: bool = False
    is_stock_confidence: float = 0.0
    is_evidence: Optional[bool] = None
    evidence_reason: Optional[str] = None

    # Status tracking
    download_status: DownloadStatus = "pending"
    error_message: Optional[str] = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now())

    model_config = {"arbitrary_types_allowed": True}

    def display_caption(self) -> str:
        """Return the best caption signal (figcaption > caption > alt_text)."""
        return self.figcaption or self.caption or self.alt_text or ""
