"""HTML → MediaCandidate harvester.

Pure (no network). Walks the article HTML and emits raw candidate URLs with
provenance (which selector found them) plus surrounding-text context for the
classifier downstream.

Harvest order, most reliable first:
    1. og:image / twitter:image meta tags
    2. JSON-LD NewsArticle.image / ImageObject (via extruct)
    3. <picture><source srcset> highest-resolution candidate
    4. <img> with lazy-load attrs (src, data-src, data-original, data-lazy-src,
       data-srcset, srcset)
    5. <figure><figcaption> for caption association
    6. Gallery widgets (Swiper, slick-slider, fancybox)
    7. Video posters (<video poster>, YouTube/Vimeo iframes)
    8. inline style="background-image:url(...)"

Tracking-pixel / icon / logo URLs are filtered out heuristically.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse

import structlog
from bs4 import BeautifulSoup, Tag

from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings

log = structlog.get_logger()


# Heuristic blocklist — URL fragments that almost always indicate non-article media.
# Mix of:
#   - decorative chrome (icons, avatars, logos, favicons)
#   - blank/spacer images
#   - third-party analytics + ad-network beacons that pose as <img> elements
#     (Taboola log endpoints, Facebook Pixel, GTM, Outbrain, DoubleClick, ...)
_URL_FRAGMENT_BLOCKLIST = (
    # Decorative chrome
    "/icons/", "/avatar/", "/avatars/", "/logo", "/favicon",
    "1x1.gif", "pixel.png", "spacer.gif", "blank.gif",
    "/social-icons/", "/share-icons/", "/tracking/",
    "/ads/", "/sponsor/",
    # Author / journalist profile images (prevent harvesting byline headshots)
    "/author/", "/authors/", "/reporter/", "/reporters/",
    "/staff/", "/journalist/", "/journalists/",
    "/contributor/", "/contributors/",
    # Tracking pixels / analytics beacons
    "trc.taboola.com",
    "/cdn-cgi/l/email-protection",
    "facebook.com/tr",
    "connect.facebook.net",
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "outbrain.com/loader",
    "amplitude.com",
    "?ev=pageview",
    "&ev=pageview",
    "noscript=1",
    "/beacon",
    "/pixel.gif",
    "/p.gif",
    "/log?",
    "/log/",
    "/collect?",
)

_BG_IMAGE_RE = re.compile(r"background-image:\s*url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
_YT_ID_RE = re.compile(r"(?:youtube\.com/(?:watch\?v=|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_VIMEO_ID_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)")

# Class/id patterns that identify author/byline DOM containers.
# Images nested inside these nodes are journalist headshots, not case media.
_AUTHOR_CONTAINER_RE = re.compile(
    r"\b(?:author|byline|reporter|journalist|contributor|writer|correspondent)\b",
    re.I,
)


class MediaHarvester:
    """Stateless HTML→candidate extractor."""

    def __init__(self, settings: MediaSettings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def harvest(self, html: str, base_url: str) -> list[MediaCandidate]:
        """Extract all media candidates from the article HTML."""
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        out: list[MediaCandidate] = []
        seen_urls: set[str] = set()

        for cand in self._all_extractors(soup, base_url):
            url = cand.source_url
            if not url or url in seen_urls:
                continue
            if self._is_blocklisted(url):
                continue
            seen_urls.add(url)
            out.append(cand)
            if len(out) >= self.settings.max_images_per_article:
                break

        log.debug("media_harvest_done", base_url=base_url, count=len(out))
        return out

    # ------------------------------------------------------------------
    # Extractor pipeline (each returns Iterable[MediaCandidate])
    # ------------------------------------------------------------------

    def _all_extractors(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        # Page-scoped extractors run on the full document — meta tags live in
        # <head>, JSON-LD can sit anywhere, both are page-level by convention.
        yield from self._extract_meta_tags(soup, base_url)
        yield from self._extract_jsonld(soup, base_url)
        # DOM-scoped extractors run inside the article-content root if we can
        # find one. This excludes sidebars, "recommended for you" widgets, and
        # related-article rails that otherwise pollute the case media list.
        # Fall back to the full document when no content root is identifiable.
        content_root = self._find_content_root(soup) or soup
        yield from self._extract_picture_sources(content_root, base_url)
        yield from self._extract_lazy_images(content_root, base_url)
        yield from self._extract_figures(content_root, base_url)
        yield from self._extract_video_posters(content_root, base_url)
        yield from self._extract_bg_images(content_root, base_url)

    def _find_content_root(self, soup: BeautifulSoup) -> Optional[Tag]:
        """Locate the article-content subtree, or return None if not present.

        Tries semantic HTML5 first (``<article>``, ``<main>``,
        ``[role="main"]``), then a small set of common publisher class/id
        substrings.  If multiple ``<article>`` tags exist, picks the largest
        by descendant count — heuristic for "the actual story" vs short
        related-article cards.
        """
        # 1. <article>
        articles = soup.find_all("article")
        if articles:
            if len(articles) == 1:
                return articles[0]
            return max(articles, key=lambda a: len(a.find_all(True)))
        # 2. <main>
        main = soup.find("main")
        if main is not None:
            return main
        # 3. role="main"
        role_main = soup.find(attrs={"role": "main"})
        if role_main is not None:
            return role_main
        # 4. Common publisher class/id substrings
        for sel in (
            {"id": re.compile(r"article|story|main", re.I)},
            {"class": re.compile(r"article-body|article-content|story-body|main-content", re.I)},
        ):
            node = soup.find(["div", "section"], attrs=sel)
            if node is not None:
                return node
        return None

    def _extract_meta_tags(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        meta_specs = [
            ("og:image", "og:image:alt"),
            ("og:image:secure_url", "og:image:alt"),
            ("twitter:image", "twitter:image:alt"),
            ("twitter:image:src", "twitter:image:alt"),
        ]
        for prop, alt_prop in meta_specs:
            tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if not tag:
                continue
            url = (tag.get("content") or "").strip()
            if not url:
                continue
            alt_tag = (
                soup.find("meta", property=alt_prop)
                or soup.find("meta", attrs={"name": alt_prop})
            )
            alt = (alt_tag.get("content").strip() if alt_tag and alt_tag.get("content") else None)
            yield self._mk_candidate(
                source_article_url=base_url,
                source_url=urljoin(base_url, url),
                discovery_selector=f"meta:{prop}",
                alt_text=alt,
            )

    def _extract_jsonld(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            text = script.string or script.get_text() or ""
            if not text.strip():
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            for img_url, caption in self._jsonld_images(data):
                yield self._mk_candidate(
                    source_article_url=base_url,
                    source_url=urljoin(base_url, img_url),
                    discovery_selector="jsonld:image",
                    caption=caption,
                )

    def _jsonld_images(self, data: Any) -> Iterable[tuple[str, Optional[str]]]:
        """Recursively walk JSON-LD looking for image URLs + captions."""
        if isinstance(data, dict):
            img = data.get("image")
            if img:
                yield from self._jsonld_image_block(img, data.get("caption"))
            for v in data.values():
                if isinstance(v, (dict, list)):
                    yield from self._jsonld_images(v)
        elif isinstance(data, list):
            for item in data:
                yield from self._jsonld_images(item)

    def _jsonld_image_block(self, img: Any, parent_caption: Any) -> Iterable[tuple[str, Optional[str]]]:
        if isinstance(img, str):
            yield img, parent_caption if isinstance(parent_caption, str) else None
        elif isinstance(img, list):
            for sub in img:
                yield from self._jsonld_image_block(sub, parent_caption)
        elif isinstance(img, dict):
            url = img.get("url") or img.get("contentUrl")
            caption = img.get("caption") or img.get("description") or parent_caption
            if url:
                yield url, caption if isinstance(caption, str) else None

    def _extract_picture_sources(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        for picture in soup.find_all("picture"):
            best_url = None
            best_w = 0
            for source in picture.find_all("source"):
                srcset = source.get("srcset") or source.get("data-srcset") or ""
                for url, w in self._parse_srcset(srcset):
                    if w > best_w:
                        best_w = w
                        best_url = url
            if best_url:
                img = picture.find("img")
                yield self._mk_candidate(
                    source_article_url=base_url,
                    source_url=urljoin(base_url, best_url),
                    discovery_selector="picture:source",
                    alt_text=img.get("alt") if img else None,
                    width=best_w,
                )

    def _extract_lazy_images(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        lazy_attrs = (
            "data-src", "data-original", "data-lazy-src",
            "data-hi-res-src", "data-image-src",
        )
        for img in soup.find_all("img"):
            # Skip journalist/author headshots embedded in byline containers.
            if self._is_author_container(img):
                continue
            url = None
            # Prefer high-res lazy-load attrs over visible src
            for attr in lazy_attrs:
                if img.get(attr):
                    url = img.get(attr).strip()
                    break
            srcset = img.get("srcset") or img.get("data-srcset") or ""
            if srcset:
                best = self._parse_srcset_best(srcset)
                if best:
                    url = best[0]
            if not url:
                url = (img.get("src") or "").strip()
            if not url or url.startswith("data:"):
                continue
            width = self._parse_int(img.get("width"))
            height = self._parse_int(img.get("height"))
            # If this <img> lives inside a <figure>, propagate the figcaption
            # so it isn't lost to URL-dedup against _extract_figures().
            figcaption = None
            parent_fig = img.find_parent("figure")
            if parent_fig is not None:
                cap_tag = parent_fig.find("figcaption")
                if cap_tag is not None:
                    figcaption = cap_tag.get_text(strip=True) or None
            yield self._mk_candidate(
                source_article_url=base_url,
                source_url=urljoin(base_url, url),
                discovery_selector=(
                    "figure" if parent_fig is not None
                    else ("img:lazy" if any(img.get(a) for a in lazy_attrs) else "img:src")
                ),
                alt_text=(img.get("alt") or None),
                figcaption=figcaption,
                width=width,
                height=height,
            )

    def _extract_figures(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        for figure in soup.find_all("figure"):
            if self._is_author_container(figure):
                continue
            img = figure.find("img")
            if not img:
                continue
            url = (
                img.get("data-src") or img.get("data-original") or img.get("src") or ""
            ).strip()
            if not url:
                continue
            figcap = figure.find("figcaption")
            caption = figcap.get_text(strip=True) if figcap else None
            yield self._mk_candidate(
                source_article_url=base_url,
                source_url=urljoin(base_url, url),
                discovery_selector="figure",
                alt_text=img.get("alt"),
                figcaption=caption,
            )

    def _extract_video_posters(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        # <video poster>
        for video in soup.find_all("video"):
            poster = video.get("poster")
            if poster:
                yield self._mk_candidate(
                    source_article_url=base_url,
                    source_url=urljoin(base_url, poster),
                    discovery_selector="video:poster",
                )
        # YouTube / Vimeo iframes → derive thumbnail
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src") or ""
            yt = _YT_ID_RE.search(src)
            if yt:
                yield self._mk_candidate(
                    source_article_url=base_url,
                    source_url=f"https://img.youtube.com/vi/{yt.group(1)}/maxresdefault.jpg",
                    discovery_selector="iframe:youtube",
                )
                continue
            vm = _VIMEO_ID_RE.search(src)
            if vm:
                # Vimeo thumbs require API; emit URL as-is for now
                yield self._mk_candidate(
                    source_article_url=base_url,
                    source_url=f"https://vumbnail.com/{vm.group(1)}.jpg",
                    discovery_selector="iframe:vimeo",
                )

    def _extract_bg_images(self, soup: BeautifulSoup, base_url: str) -> Iterable[MediaCandidate]:
        # inline style attributes
        for tag in soup.find_all(style=True):
            style = tag.get("style") or ""
            for m in _BG_IMAGE_RE.finditer(style):
                url = m.group(1).strip()
                if url and not url.startswith("data:"):
                    yield self._mk_candidate(
                        source_article_url=base_url,
                        source_url=urljoin(base_url, url),
                        discovery_selector="style:background-image",
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_srcset(srcset: str) -> Iterable[tuple[str, int]]:
        """Parse `srcset` string → list of (url, width_in_px)."""
        if not srcset:
            return []
        out: list[tuple[str, int]] = []
        for part in srcset.split(","):
            tokens = part.strip().split()
            if not tokens:
                continue
            url = tokens[0]
            width = 0
            if len(tokens) > 1:
                desc = tokens[1]
                if desc.endswith("w"):
                    try:
                        width = int(desc[:-1])
                    except ValueError:
                        width = 0
                elif desc.endswith("x"):
                    # Pixel-density: convert to ~width by guessing 1024 base
                    try:
                        width = int(float(desc[:-1]) * 1024)
                    except ValueError:
                        width = 0
            out.append((url, width))
        return out

    @classmethod
    def _parse_srcset_best(cls, srcset: str) -> Optional[tuple[str, int]]:
        candidates = list(cls._parse_srcset(srcset))
        if not candidates:
            return None
        return max(candidates, key=lambda t: t[1])

    @staticmethod
    def _parse_int(v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(str(v).strip())
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _is_blocklisted(url: str) -> bool:
        u = url.lower()
        return any(frag in u for frag in _URL_FRAGMENT_BLOCKLIST)

    @staticmethod
    def _is_author_container(tag: Tag) -> bool:
        """Return True when tag is nested inside an author/byline DOM container.

        Walks up to 8 parent levels — enough to catch typical byline structures
        (img → span → div.author-info → article) without scanning the whole
        document tree on every image.
        """
        for depth, parent in enumerate(tag.parents):
            if depth >= 8:
                break
            if not isinstance(parent, Tag):
                continue
            classes = " ".join(parent.get("class") or [])
            tag_id = parent.get("id") or ""
            if _AUTHOR_CONTAINER_RE.search(classes) or _AUTHOR_CONTAINER_RE.search(tag_id):
                return True
        return False

    @staticmethod
    def _mk_candidate(**kwargs: Any) -> MediaCandidate:
        return MediaCandidate(**kwargs)
