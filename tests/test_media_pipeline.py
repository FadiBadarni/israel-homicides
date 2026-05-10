"""Unit tests for the media subsystem.

Covers: harvester, classifier, dedup, splitter, and the MediaPipeline
orchestrator (with the downloader monkey-patched to avoid network I/O).
"""
from __future__ import annotations

import pytest

from crime_pipeline.media import (
    ArticleContext,
    MediaCandidate,
    MediaPipeline,
    MediaSettings,
)
from crime_pipeline.media.classifier import MediaClassifier
from crime_pipeline.media.dedup import (
    cluster_across_sources,
    dedup_within_article,
    media_id_for,
    select_canonical,
)
from crime_pipeline.media.harvester import MediaHarvester
from crime_pipeline.media.splitter import split_media


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> MediaSettings:
    return MediaSettings(
        max_images_per_article=20,
        max_images_per_case=80,
        enable_clip_classifier=False,
        enable_face_detection=False,
    )


@pytest.fixture
def article_html_full() -> str:
    """Article-style HTML with og:image, JSON-LD, lazy-img, figure, and a video."""
    return """
    <html>
      <head>
        <meta property="og:image" content="https://example.com/lead.jpg" />
        <meta property="og:image:alt" content="The late Bakr Yassin" />
        <meta property="twitter:image" content="https://example.com/twitter.jpg" />
        <script type="application/ld+json">
          {
            "@type": "NewsArticle",
            "image": {"@type":"ImageObject","url":"https://example.com/jsonld.jpg",
                      "caption":"Crime scene in Arraba"}
          }
        </script>
      </head>
      <body>
        <figure>
          <img src="https://example.com/figure.jpg" alt="suspect at hearing" />
          <figcaption>Suspect at the remand hearing</figcaption>
        </figure>
        <img data-src="https://example.com/lazy.jpg" alt="general image" />
        <picture>
          <source srcset="https://example.com/small.jpg 320w,
                          https://example.com/big.jpg 1600w" />
          <img src="https://example.com/fallback.jpg" alt="responsive" />
        </picture>
        <iframe src="https://www.youtube.com/embed/ABCDEFGHIJK"></iframe>
        <div style="background-image: url('https://example.com/bg.jpg')"></div>
        <img src="https://cdn.example.com/icons/share.png" alt="share" />
      </body>
    </html>
    """


# ---------------------------------------------------------------------------
# Harvester
# ---------------------------------------------------------------------------


class TestHarvester:
    def test_harvest_finds_all_selectors(self, settings, article_html_full):
        h = MediaHarvester(settings)
        cands = h.harvest(article_html_full, "https://news.example.com/story/1")
        urls = {c.source_url for c in cands}
        assert "https://example.com/lead.jpg" in urls
        assert "https://example.com/twitter.jpg" in urls
        assert "https://example.com/jsonld.jpg" in urls
        assert "https://example.com/figure.jpg" in urls
        assert "https://example.com/lazy.jpg" in urls
        assert "https://example.com/big.jpg" in urls  # picked highest-res from srcset
        assert "https://example.com/bg.jpg" in urls
        assert "https://img.youtube.com/vi/ABCDEFGHIJK/maxresdefault.jpg" in urls

    def test_blocklist_drops_icons(self, settings, article_html_full):
        h = MediaHarvester(settings)
        cands = h.harvest(article_html_full, "https://news.example.com/story/1")
        urls = {c.source_url for c in cands}
        assert "https://cdn.example.com/icons/share.png" not in urls

    def test_figure_caption_propagates(self, settings, article_html_full):
        h = MediaHarvester(settings)
        cands = h.harvest(article_html_full, "https://news.example.com/story/1")
        figure = next(c for c in cands if c.source_url == "https://example.com/figure.jpg")
        assert figure.figcaption == "Suspect at the remand hearing"

    def test_og_image_alt_with_no_content_does_not_crash(self, settings):
        # Regression: og:image:alt with no content attr must not raise.
        html = """
        <html><head>
          <meta property="og:image" content="https://example.com/lead.jpg" />
          <meta property="og:image:alt" />
        </head><body></body></html>
        """
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://news.example.com/story/1")
        assert any(c.source_url == "https://example.com/lead.jpg" for c in cands)

    def test_relative_urls_resolved(self, settings):
        html = """<html><body><img src="/img/local.jpg" alt="x"/></body></html>"""
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://news.example.com/story/1")
        urls = {c.source_url for c in cands}
        assert "https://news.example.com/img/local.jpg" in urls

    def test_data_uri_skipped(self, settings):
        html = """<html><body>
          <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA" alt="inline"/>
          <img src="https://example.com/real.jpg"/>
        </body></html>"""
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://x.com/")
        urls = {c.source_url for c in cands}
        assert urls == {"https://example.com/real.jpg"}

    def test_max_images_per_article_cap(self):
        settings = MediaSettings(max_images_per_article=3)
        imgs = "".join(
            f'<img src="https://x.com/{i}.jpg"/>' for i in range(10)
        )
        html = f"<html><body>{imgs}</body></html>"
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://x.com/")
        assert len(cands) == 3

    def test_blocklist_drops_tracking_pixels(self, settings):
        """Fix 1 regression: Taboola/FB Pixel/GTM beacons must not pass."""
        html = """
        <html><body>
          <img src="https://trc.taboola.com/1063110/log/3/unip?en=page_view" />
          <img src="https://www.facebook.com/tr?id=12345&ev=PageView&noscript=1" />
          <img src="https://www.googletagmanager.com/gtag/js?id=G-12345" />
          <img src="https://stats.g.doubleclick.net/r/collect?v=1" />
          <img src="https://example.com/legit_photo.jpg" />
        </body></html>
        """
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://news.example.com/")
        urls = {c.source_url for c in cands}
        assert "https://example.com/legit_photo.jpg" in urls
        # All five tracking patterns should be filtered.
        assert not any("taboola" in u for u in urls)
        assert not any("facebook.com/tr" in u for u in urls)
        assert not any("googletagmanager" in u for u in urls)
        assert not any("doubleclick" in u for u in urls)

    def test_dom_extractors_scoped_to_article(self, settings):
        """Fix 2: <img>s outside <article> (sidebar / recommendations) must be skipped."""
        html = """
        <html>
          <head>
            <meta property="og:image" content="https://example.com/lead.jpg"/>
          </head>
          <body>
            <aside>
              <img src="https://example.com/sidebar1.jpg"/>
              <img src="https://example.com/sidebar2.jpg"/>
            </aside>
            <article>
              <figure>
                <img src="https://example.com/inarticle.jpg"/>
                <figcaption>caption inside</figcaption>
              </figure>
            </article>
            <div class="recommended">
              <img src="https://example.com/recommendation.jpg"/>
            </div>
          </body>
        </html>
        """
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://news.example.com/")
        urls = {c.source_url for c in cands}
        # head meta still flows through (page-level)
        assert "https://example.com/lead.jpg" in urls
        # article-body image included
        assert "https://example.com/inarticle.jpg" in urls
        # sidebar + recommendations excluded
        assert "https://example.com/sidebar1.jpg" not in urls
        assert "https://example.com/sidebar2.jpg" not in urls
        assert "https://example.com/recommendation.jpg" not in urls

    def test_no_article_falls_back_to_full_document(self, settings):
        """Fix 2 fallback: pages without <article>/<main> still harvest from <body>."""
        html = """
        <html><body>
          <div class="post-body">
            <img src="https://example.com/img.jpg" alt="x"/>
          </div>
        </body></html>
        """
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://x.com/")
        urls = {c.source_url for c in cands}
        # No <article>/<main>/role=main, so fallback to whole soup OR matched
        # "post-body" via class-name regex.
        assert "https://example.com/img.jpg" in urls

    def test_picks_largest_article_when_multiple(self, settings):
        """Fix 2: pages with multiple <article> tags pick the one with the most descendants."""
        html = """
        <html><body>
          <article>  <!-- short related-article card -->
            <img src="https://example.com/short.jpg"/>
          </article>
          <article>  <!-- the actual story -->
            <p>Lots of content here</p>
            <p>Even more content</p>
            <p>Plenty of paragraphs</p>
            <figure><img src="https://example.com/main.jpg"/></figure>
          </article>
        </body></html>
        """
        h = MediaHarvester(settings)
        cands = h.harvest(html, "https://x.com/")
        urls = {c.source_url for c in cands}
        assert "https://example.com/main.jpg" in urls
        assert "https://example.com/short.jpg" not in urls


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class TestClassifier:
    @pytest.mark.asyncio
    async def test_caption_name_match_yields_victim_portrait(self, settings):
        cand = MediaCandidate(
            source_article_url="https://x.com/a",
            source_url="https://x.com/img.jpg",
            discovery_selector="figure",
            figcaption="The late Bakr Yassin in undated photo",
            download_status="ok",
            sha256="a" * 64,
            phash="0123456789abcdef",
        )
        ctx = ArticleContext(
            article_url="https://x.com/a",
            victim_names=["Bakr Yassin"],
            suspect_names=[],
            city_names=["Arraba"],
        )
        c = MediaClassifier(settings)
        await c.classify(cand, ctx)
        assert cand.classification == "victim_portrait"
        assert cand.classification_confidence >= 0.9
        assert any("caption_match:victim_name" in e for e in cand.classification_evidence)

    @pytest.mark.asyncio
    async def test_stock_domain_demotes(self, settings):
        cand = MediaCandidate(
            source_article_url="https://x.com/a",
            source_url="https://www.gettyimages.com/photo/123.jpg",
            discovery_selector="meta:og:image",
            alt_text="generic illustration",
            download_status="ok",
            sha256="b" * 64,
            phash="fedcba9876543210",
        )
        ctx = ArticleContext(
            article_url="https://x.com/a",
            victim_names=["Bakr Yassin"],
        )
        c = MediaClassifier(settings)
        await c.classify(cand, ctx)
        assert cand.is_stock_photo is True
        assert cand.is_stock_confidence >= 0.9
        assert any(e.startswith("stock:domain:") for e in cand.classification_evidence)

    @pytest.mark.asyncio
    async def test_unrelated_to_article_caption_is_stock_signal(self, settings):
        cand = MediaCandidate(
            source_article_url="https://x.com/a",
            source_url="https://x.com/scene.jpg",
            discovery_selector="figure",
            figcaption="חיסול בעראבה (למצולמים אין קשר לכתבה)",
            download_status="ok",
            sha256="b" * 64,
            phash="fedcba9876543210",
        )
        ctx = ArticleContext(article_url="https://x.com/a", city_names=["עראבה"])
        c = MediaClassifier(settings)

        await c.classify(cand, ctx)

        assert cand.is_stock_photo is True
        assert cand.is_stock_confidence >= 0.8
        assert any("stock:caption:" in e for e in cand.classification_evidence)

    @pytest.mark.asyncio
    async def test_no_text_signal_returns_other_low_confidence(self, settings):
        cand = MediaCandidate(
            source_article_url="https://x.com/a",
            source_url="https://x.com/blank.jpg",
            discovery_selector="img:src",
            download_status="ok",
            sha256="c" * 64,
            phash="abcdef0123456789",
        )
        ctx = ArticleContext(article_url="https://x.com/a")
        c = MediaClassifier(settings)
        await c.classify(cand, ctx)
        assert cand.classification == "other"
        assert cand.classification_confidence < 0.5
        assert "keyword:no_text_signal" in cand.classification_evidence

    @pytest.mark.asyncio
    async def test_arabic_keywords_match(self, settings):
        cand = MediaCandidate(
            source_article_url="https://x.com/a",
            source_url="https://x.com/img.jpg",
            discovery_selector="figure",
            figcaption="جنازة الضحية في عرابة",  # funeral of the victim in Arraba
            download_status="ok",
            phash="1111222233334444",
        )
        ctx = ArticleContext(article_url="https://x.com/a")
        c = MediaClassifier(settings)
        await c.classify(cand, ctx)
        # "الضحية" (victim) keyword fires for victim_portrait,
        # "جنازة" (funeral) fires for funeral. We only assert the result is
        # not "other" — caption-only signals can land in either bucket.
        assert cand.classification != "other"

    @pytest.mark.asyncio
    async def test_failed_download_still_caption_classified(self, settings):
        cand = MediaCandidate(
            source_article_url="https://x.com/a",
            source_url="https://x.com/dead.jpg",
            discovery_selector="figure",
            figcaption="The late Bakr Yassin",
            download_status="http_error",
        )
        ctx = ArticleContext(
            article_url="https://x.com/a", victim_names=["Bakr Yassin"]
        )
        c = MediaClassifier(settings)
        await c.classify(cand, ctx)
        assert cand.classification == "victim_portrait"


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def _mk(
    sha: str | None = None,
    phash: str | None = None,
    width: int | None = None,
    height: int | None = None,
    caption: str | None = None,
    article_url: str = "https://a.example.com/x",
    image_url: str = "https://a.example.com/img.jpg",
) -> MediaCandidate:
    return MediaCandidate(
        source_article_url=article_url,
        source_url=image_url,
        discovery_selector="img:src",
        figcaption=caption,
        download_status="ok",
        sha256=sha,
        phash=phash,
        width=width,
        height=height,
    )


class TestDedup:
    def test_within_article_sha256_collapses(self, settings):
        cands = [
            _mk(sha="aaa", caption=None),
            _mk(sha="aaa", caption="richer caption"),  # same image, better caption
            _mk(sha="bbb", caption=None),
        ]
        out = dedup_within_article(cands, settings)
        assert len(out) == 2
        keepers = {c.figcaption for c in out}
        # Richer caption was promoted over None.
        assert "richer caption" in keepers

    def test_cross_source_phash_clusters(self, settings):
        # Two near-identical phashes (hamming = 0) and one totally different
        cands = [
            _mk(sha="aaa", phash="0000000000000000", article_url="https://a.com/1",
                image_url="https://a.com/i.jpg"),
            _mk(sha="zzz", phash="0000000000000000", article_url="https://b.com/2",
                image_url="https://b.com/i.jpg"),
            _mk(sha="ccc", phash="ffffffffffffffff", article_url="https://c.com/3",
                image_url="https://c.com/i.jpg"),
        ]
        clusters = cluster_across_sources(cands, settings)
        # The two identical-phash candidates merge; the third stays alone.
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 2]

    def test_select_canonical_prefers_largest_then_caption(self, settings):
        a = _mk(sha="a", phash="0" * 16, width=200, height=200, caption="x")
        b = _mk(sha="b", phash="0" * 16, width=1600, height=900, caption=None)
        c = _mk(sha="c", phash="0" * 16, width=1600, height=900, caption="rich")
        rep = select_canonical([a, b, c])
        assert rep is c  # same area as b, but c has caption

    def test_media_id_uses_phash(self, settings):
        cands = [_mk(sha="a", phash="abcdef0123456789")]
        assert media_id_for(cands) == "phash:abcdef0123456789"

    def test_media_id_falls_back_to_sha(self, settings):
        cands = [_mk(sha="abcdef0123456789abcdef0123456789", phash=None)]
        out = media_id_for(cands)
        assert out.startswith("sha:")


# ---------------------------------------------------------------------------
# Splitter
# ---------------------------------------------------------------------------


class TestSplitter:
    def test_caption_name_match_routes_to_evidence(self, settings):
        cand = _mk(caption="Bakr Yassin smiling for the camera")
        cand.classification = "victim_portrait"
        ctx = ArticleContext(article_url="x", victim_names=["Bakr Yassin"])
        media, evidence = split_media([cand], ctx, settings)
        assert evidence and not media

    def test_generic_stock_demotes(self, settings):
        cand = _mk(caption="for illustration")
        cand.classification = "generic_stock"
        ctx = ArticleContext(article_url="x")
        media, evidence = split_media([cand], ctx, settings)
        assert media and not evidence
        assert media[0].evidence_reason == "category:generic_stock"

    def test_high_conf_stock_demotes(self, settings):
        cand = _mk()
        cand.classification = "victim_portrait"  # mislabel that should be overridden
        cand.is_stock_photo = True
        cand.is_stock_confidence = 0.95
        ctx = ArticleContext(article_url="x", victim_names=[])
        media, evidence = split_media([cand], ctx, settings)
        assert media and not evidence

    def test_low_conf_stock_kept_with_warning(self, settings):
        cand = _mk()
        cand.classification = "crime_scene"
        cand.is_stock_photo = True
        cand.is_stock_confidence = 0.3
        cand.discovery_selector = "img:src"
        ctx = ArticleContext(article_url="x", city_names=[])
        media, evidence = split_media([cand], ctx, settings)
        assert evidence and not media
        assert "low_confidence_stock" in (evidence[0].evidence_reason or "")

    def test_og_image_lead_is_evidence(self, settings):
        cand = _mk()
        cand.classification = "other"
        cand.discovery_selector = "meta:og:image"
        ctx = ArticleContext(article_url="x")
        media, evidence = split_media([cand], ctx, settings)
        assert evidence and not media
        assert evidence[0].evidence_reason == "og_image_lead"

    def test_victim_portrait_with_city_is_evidence(self, settings):
        """Hebrew/Arabic captions often use generic words like 'הנרצח'/'الضحية'
        instead of the full name. A confident portrait classification + the
        case city in the caption should be enough."""
        cand = _mk(caption="הנרצח בעראבה")
        cand.classification = "victim_portrait"
        cand.classification_confidence = 0.65
        ctx = ArticleContext(
            article_url="x",
            victim_names=["בכר יאסין"],  # NOT in the caption
            city_names=["עראבה"],         # IS in the caption (substring match)
        )
        media, evidence = split_media([cand], ctx, settings)
        assert evidence and not media
        assert "category:victim_portrait" in (evidence[0].evidence_reason or "")
        assert "city:" in (evidence[0].evidence_reason or "")

    def test_low_confidence_portrait_without_city_is_decorative(self, settings):
        """Without a city anchor and only mid confidence, a portrait stays decorative
        — guards against turning every captioned img in unrelated articles into evidence."""
        cand = _mk(caption="some random portrait")
        cand.classification = "victim_portrait"
        cand.classification_confidence = 0.65
        ctx = ArticleContext(
            article_url="x",
            victim_names=["Bakr Yassin"],
            city_names=["Arraba"],   # not in caption
        )
        media, evidence = split_media([cand], ctx, settings)
        assert media and not evidence

    def test_very_high_confidence_portrait_clears_without_city(self, settings):
        """When the classifier already had a caption-name match (conf >= 0.85),
        the city check is redundant — the strongest signal already fired."""
        cand = _mk(caption="some caption text")
        cand.classification = "victim_portrait"
        cand.classification_confidence = 0.92
        ctx = ArticleContext(article_url="x", city_names=[])
        media, evidence = split_media([cand], ctx, settings)
        assert evidence and not media
        assert "high_conf" in (evidence[0].evidence_reason or "")


# ---------------------------------------------------------------------------
# Orchestrator (MediaPipeline) — end-to-end with mocked downloader
# ---------------------------------------------------------------------------


def _patch_downloader(monkeypatch, hashes_by_url: dict[str, tuple[str, str]]) -> None:
    """Replace MediaDownloader.fetch_many with a deterministic stub.

    ``hashes_by_url`` maps source_url → (sha256, phash). Candidates whose URL
    isn't in the map are returned with download_status='http_error'.
    """
    async def fake_fetch_many(self, candidates, client=None):
        for cand in candidates:
            if cand.source_url in hashes_by_url:
                sha, phash = hashes_by_url[cand.source_url]
                cand.sha256 = sha
                cand.phash = phash
                cand.download_status = "ok"
            else:
                cand.download_status = "http_error"
        return candidates

    from crime_pipeline.media.downloader import MediaDownloader
    monkeypatch.setattr(MediaDownloader, "fetch_many", fake_fetch_many)


@pytest.mark.asyncio
async def test_pipeline_end_to_end(monkeypatch, settings):
    """Two articles, one shared portrait of the victim → one canonical evidence."""
    article_a = """
    <html><head>
      <meta property="og:image" content="https://cdn.com/portrait.jpg" />
      <meta property="og:image:alt" content="The late Bakr Yassin" />
    </head><body>
      <figure>
        <img src="https://cdn.com/scene.jpg" alt="scene" />
        <figcaption>Crime scene in Arraba</figcaption>
      </figure>
    </body></html>
    """
    article_b = """
    <html><head>
      <meta property="og:image" content="https://other.com/portrait_mirror.jpg" />
    </head><body>
      <figure>
        <img src="https://other.com/stock.jpg" alt="illustration" />
        <figcaption>illustration</figcaption>
      </figure>
    </body></html>
    """

    # Shared portrait gets the same phash (cross-source dedup).
    # Scene + illustration get distinct phashes.
    _patch_downloader(monkeypatch, {
        "https://cdn.com/portrait.jpg":         ("p_sha", "0000000000000000"),
        "https://other.com/portrait_mirror.jpg":("m_sha", "0000000000000000"),
        "https://cdn.com/scene.jpg":            ("s_sha", "ffffffffffffffff"),
        "https://other.com/stock.jpg":          ("k_sha", "aaaa5555aaaa5555"),
    })

    pipe = MediaPipeline(settings)
    ctx = ArticleContext(
        article_url="https://news.example.com/a",
        victim_names=["Bakr Yassin"],
        city_names=["Arraba"],
    )

    media, evidence = await pipe.run_for_case(
        [
            {"raw_html": article_a, "url": "https://news.example.com/a"},
            {"raw_html": article_b, "url": "https://news.example.com/b"},
        ],
        ctx,
    )

    # Portrait should fold to ONE canonical (cross-source dedup) classified
    # via og:image:alt → caption-name-match → victim_portrait.
    portraits = [m for m in evidence if m.type == "victim_portrait"]
    assert len(portraits) == 1, f"expected 1 portrait, got {len(portraits)}: {[p.model_dump() for p in evidence]}"
    p = portraits[0]
    assert p.appearance_count == 2
    # Mirror_urls captures the other publisher's hosted copy.
    all_urls = {p.primary_url, *p.mirror_urls}
    assert "https://cdn.com/portrait.jpg" in all_urls
    assert "https://other.com/portrait_mirror.jpg" in all_urls
    # Both source articles tracked.
    assert set(p.source_article_urls) == {
        "https://news.example.com/a",
        "https://news.example.com/b",
    }
    assert p.confidence >= 0.9


@pytest.mark.asyncio
async def test_pipeline_promotes_og_image_signal_across_cluster(monkeypatch, settings):
    """Fix 3 regression: an og:image lead in any cluster member survives canonical-rep selection.

    Two articles from DIFFERENT publishers carrying the same image. The body
    figure has dimensions (would be picked as rep by area), the meta og:image
    does not. Without the og-signal-promotion fix, the rep's
    discovery_selector ends up "figure" and the splitter's og_image_lead rule
    never fires. Cross-publisher corroboration also satisfies the
    "single_publisher_unverified" demotion check.
    """
    article_a = """
    <html><head>
      <meta property="og:image" content="https://shared.com/photo.jpg" />
    </head><body>
      <article><p>Lead story</p></article>
    </body></html>
    """
    article_b = """
    <html><body>
      <article>
        <figure>
          <img src="https://shared.com/photo.jpg" width="1600" height="900"/>
          <figcaption>A photo</figcaption>
        </figure>
      </article>
    </body></html>
    """
    _patch_downloader(monkeypatch, {
        "https://shared.com/photo.jpg": ("h1", "1111111111111111"),
    })
    pipe = MediaPipeline(settings)
    ctx = ArticleContext(article_url="https://x.com/", victim_names=[])
    media, evidence = await pipe.run_for_case(
        [
            # Two distinct publishers — corroboration passes.
            {"raw_html": article_a, "url": "https://haaretz.co.il/news/a"},
            {"raw_html": article_b, "url": "https://mako.co.il/news/b"},
        ],
        ctx,
    )
    all_items = media + evidence
    assert len(all_items) == 1
    cm = all_items[0]
    assert cm.appearance_count == 2
    assert cm.is_evidence is True, f"og_image_lead rule did not fire — got is_evidence={cm.is_evidence}, reason={cm.evidence_reason}"
    assert cm.evidence_reason == "og_image_lead"


@pytest.mark.asyncio
async def test_pipeline_demotes_single_publisher_og_image(monkeypatch, settings):
    """An og:image from a SINGLE publisher must be demoted to decorative.

    This protects against publisher-brand images, column hero photos, and
    byline-style author headshots being silently promoted as case evidence.
    Real lead photos cross publishers; brand chrome doesn't.
    """
    article = """
    <html><head>
      <meta property="og:image" content="https://haaretz.com/column-hero.jpg" />
    </head><body>
      <article><p>A column piece</p></article>
    </body></html>
    """
    _patch_downloader(monkeypatch, {
        "https://haaretz.com/column-hero.jpg": ("h1", "1111111111111111"),
    })
    pipe = MediaPipeline(settings)
    ctx = ArticleContext(article_url="https://x.com/", victim_names=[])
    media, evidence = await pipe.run_for_case(
        [{"raw_html": article, "url": "https://haaretz.co.il/news/single"}],
        ctx,
    )
    # Single-publisher og:image must NOT land in media_evidence.
    assert evidence == []
    assert len(media) == 1
    assert media[0].is_evidence is False
    assert media[0].evidence_reason == "og_image_lead:single_publisher_unverified"
    # appearance_count reflects distinct articles, not cluster size.
    assert media[0].appearance_count == 1


@pytest.mark.asyncio
async def test_pipeline_drops_failed_downloads(monkeypatch, settings):
    article = """
    <html><head>
      <meta property="og:image" content="https://cdn.com/missing.jpg" />
    </head><body>
      <article>
        <figure>
          <img src="https://cdn.com/scene.jpg" />
          <figcaption>Crime scene in Arraba</figcaption>
        </figure>
      </article>
    </body></html>
    """
    _patch_downloader(monkeypatch, {
        "https://cdn.com/scene.jpg": ("s_sha", "ffffffffffffffff"),
    })
    pipe = MediaPipeline(settings)
    ctx = ArticleContext(article_url="https://x.com/", city_names=["Arraba"])

    media, evidence = await pipe.run_for_case(
        [{"raw_html": article, "url": "https://news.example.com/a"}],
        ctx,
    )

    all_items = media + evidence
    assert len(all_items) == 1
    assert all_items[0].status == "available"
    assert all_items[0].primary_url == "https://cdn.com/scene.jpg"


@pytest.mark.asyncio
async def test_pipeline_disabled_returns_empty(settings):
    settings.enabled = False
    pipe = MediaPipeline(settings)
    ctx = ArticleContext(article_url="x")
    media, evidence = await pipe.run_for_case(
        [{"raw_html": "<html></html>", "url": "https://x.com/"}], ctx
    )
    assert media == [] and evidence == []


@pytest.mark.asyncio
async def test_pipeline_no_articles_returns_empty(settings):
    pipe = MediaPipeline(settings)
    ctx = ArticleContext(article_url="x")
    media, evidence = await pipe.run_for_case([], ctx)
    assert media == [] and evidence == []
