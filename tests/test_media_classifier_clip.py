from __future__ import annotations

from pathlib import Path

import pytest

from crime_pipeline.media.classifier import (
    ArticleContext,
    MediaClassifier,
    _ClipImageDecodeError,
)
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings


@pytest.mark.asyncio
async def test_clip_classifier_uses_cached_bytes_and_records_embedding(tmp_path):
    settings = MediaSettings(
        enable_clip_classifier=True,
        enable_face_detection=False,
        keyword_confidence_threshold=0.95,
        clip_confidence_threshold=0.25,
    )
    image_path = tmp_path / "crime-clip-image.bin"
    image_path.write_bytes(b"fake-image-bytes")

    cand = MediaCandidate(
        source_article_url="https://x.com/a",
        source_url="https://x.com/clip.jpg",
        discovery_selector="img:src",
        download_status="ok",
        bytes_ref=str(image_path),
        sha256="d" * 64,
        phash="1234567890abcdef",
    )
    ctx = ArticleContext(
        article_url="https://x.com/a",
        victim_names=["Bakr Yassin"],
        city_names=["Arraba"],
    )

    class FakeClipRuntime:
        def __init__(self):
            self.calls = []

        def classify(self, image_path, prompts):
            self.calls.append((image_path, prompts))
            return "crime_scene", 0.81, [0.1, 0.2, 0.3]

    clip_runtime = FakeClipRuntime()
    c = MediaClassifier(settings)
    c._load_clip_runtime = lambda: clip_runtime

    await c.classify(cand, ctx)

    assert clip_runtime.calls == [(str(image_path), c._clip_prompts(ctx))]
    assert cand.classification == "crime_scene"
    assert cand.classifier_tier == "clip"
    assert cand.classification_confidence == pytest.approx(0.81)
    assert cand.clip_embedding == [0.1, 0.2, 0.3]
    assert "clip:crime_scene:0.81" in cand.classification_evidence


@pytest.mark.asyncio
async def test_clip_classifier_graceful_degrade_when_open_clip_missing(tmp_path):
    """When open_clip isn't installed, the classifier MUST NOT raise — it
    appends 'clip:not_installed' evidence and lets the keyword tier own
    the classification. This is the protection that lets the [vision]
    extras stay optional."""
    settings = MediaSettings(
        enable_clip_classifier=True,
        enable_face_detection=False,
        keyword_confidence_threshold=0.95,
        clip_confidence_threshold=0.25,
    )
    image_path = tmp_path / "img.bin"
    image_path.write_bytes(b"x")

    cand = MediaCandidate(
        source_article_url="https://x.com/a",
        source_url="https://x.com/clip.jpg",
        discovery_selector="img:src",
        download_status="ok",
        bytes_ref=str(image_path),
        sha256="f" * 64,
        phash="0123456789abcdef",
    )
    ctx = ArticleContext(article_url="https://x.com/a")

    c = MediaClassifier(settings)
    # Simulate the actual graceful-degrade trigger — open_clip not importable.
    def _raise_not_installed():
        raise ModuleNotFoundError("No module named 'open_clip'")
    c._load_clip_runtime = _raise_not_installed

    # Must not raise.
    await c.classify(cand, ctx)

    # Evidence trail records the missing dep, candidate stays keyword-typed.
    assert "clip:not_installed" in cand.classification_evidence
    assert cand.classifier_tier == "keyword"
    assert cand.clip_embedding is None

    # And on a SECOND call, the classifier MUST NOT retry the import — caching
    # the failure prevents per-image retry storms when torch isn't installed.
    cand2 = MediaCandidate(
        source_article_url="https://x.com/a",
        source_url="https://x.com/clip2.jpg",
        discovery_selector="img:src",
        download_status="ok",
        bytes_ref=str(image_path),
        sha256="g" * 64,
        phash="fedcba9876543210",
    )
    # Replace the loader with a tripwire — if it gets called, the cache broke.
    def _tripwire():
        raise AssertionError("loader retried after first failure — caching is broken")
    c._load_clip_runtime = _tripwire

    await c.classify(cand2, ctx)
    assert "clip:unavailable" in cand2.classification_evidence


@pytest.mark.asyncio
async def test_clip_classifier_skips_runtime_load_without_cached_bytes():
    settings = MediaSettings(
        enable_clip_classifier=True,
        enable_face_detection=False,
        keyword_confidence_threshold=0.95,
        clip_confidence_threshold=0.25,
    )
    cand = MediaCandidate(
        source_article_url="https://x.com/a",
        source_url="https://x.com/clip.jpg",
        discovery_selector="img:src",
        download_status="ok",
        sha256="e" * 64,
        phash="abcdef1234567890",
    )
    ctx = ArticleContext(article_url="https://x.com/a")

    c = MediaClassifier(settings)

    def _fail_if_called():
        raise AssertionError("CLIP runtime should not load without bytes_ref")

    c._load_clip_runtime = _fail_if_called

    await c.classify(cand, ctx)

    assert cand.classification == "other"
    assert cand.classification_confidence == 0.2
    assert cand.classifier_tier == "keyword"
    assert "clip:no_bytes_ref" in cand.classification_evidence


@pytest.mark.asyncio
async def test_clip_classifier_handles_corrupt_image_bytes(tmp_path):
    """When the CLIP runtime can't decode the image (corrupt bytes,
    decompression-bomb guard tripped, unsupported format), the classifier
    MUST NOT raise. It records `clip:image_decode_failed:...` evidence
    and lets the keyword tier own the result."""
    settings = MediaSettings(
        enable_clip_classifier=True,
        enable_face_detection=False,
        keyword_confidence_threshold=0.95,
        clip_confidence_threshold=0.25,
    )
    image_path = tmp_path / "corrupt.bin"
    image_path.write_bytes(b"not actually an image")

    cand = MediaCandidate(
        source_article_url="https://x.com/a",
        source_url="https://x.com/corrupt.jpg",
        discovery_selector="img:src",
        download_status="ok",
        bytes_ref=str(image_path),
        sha256="h" * 64,
        phash="cafef00dcafef00d",
    )
    ctx = ArticleContext(article_url="https://x.com/a")

    class CorruptClipRuntime:
        def classify(self, image_path, prompts):
            raise _ClipImageDecodeError("UnidentifiedImageError:cannot identify image file")

    c = MediaClassifier(settings)
    c._load_clip_runtime = lambda: CorruptClipRuntime()

    # Must not raise.
    await c.classify(cand, ctx)

    assert cand.clip_embedding is None
    assert any(
        e.startswith("clip:image_decode_failed:") for e in cand.classification_evidence
    ), f"missing decode-failed evidence in {cand.classification_evidence}"
    # Classifier_tier stays keyword — CLIP didn't override.
    assert cand.classifier_tier == "keyword"
