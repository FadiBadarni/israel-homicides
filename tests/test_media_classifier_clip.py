from __future__ import annotations

import pytest

from crime_pipeline.media.classifier import ArticleContext, MediaClassifier
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
    image_path = tmp_path / "clip-image.bin"
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
