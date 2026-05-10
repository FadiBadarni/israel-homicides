from __future__ import annotations

from pathlib import Path

import pytest

from crime_pipeline.media.classifier import ArticleContext, MediaClassifier
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings


@pytest.mark.asyncio
async def test_clip_classifier_uses_cached_bytes_and_records_embedding():
    settings = MediaSettings(
        enable_clip_classifier=True,
        enable_face_detection=False,
        keyword_confidence_threshold=0.95,
        clip_confidence_threshold=0.25,
    )
    image_path = Path(r"C:\Users\fadi_\AppData\Local\Temp\crime-clip-image.bin")
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
