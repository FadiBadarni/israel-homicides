"""Regression tests for Ynet JSON-LD body extraction.

The bug: Ynet's React rebuild renamed body classes from `div.article-body p`
to `.ArticleBodyComponent`. Pre-fix, EVERY Ynet article fetch returned 0
chars of body — articles flowed to extract with empty text and got dropped
silently in `_extract` (which filters `if a.fetch_status == "success" and
a.article_text`).

Fix: try canonical schema.org JSON-LD `articleBody` first, fall back to
CSS selectors. JSON-LD is immune to class-name churn.
"""
from __future__ import annotations

from crime_pipeline.scrapers.ynet import _extract_body_from_jsonld


def test_extracts_articleBody_from_single_object_jsonld() -> None:
    html = '''
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"NewsArticle",
     "headline":"X","articleBody":"This is the body text. Multiple sentences."}
    </script>
    </head><body></body></html>
    '''
    out = _extract_body_from_jsonld(html)
    assert "This is the body text" in out


def test_extracts_articleBody_from_jsonld_array() -> None:
    """Some sites emit a JSON-LD array; the article object is one element."""
    html = '''
    <script type="application/ld+json">
    [
      {"@type":"BreadcrumbList","itemListElement":[]},
      {"@type":"NewsArticle","articleBody":"Real article body here."}
    ]
    </script>
    '''
    out = _extract_body_from_jsonld(html)
    assert "Real article body here" in out


def test_returns_empty_when_no_articleBody_field() -> None:
    """JSON-LD exists but no articleBody — should fall through to selectors."""
    html = '''
    <script type="application/ld+json">
    {"@type":"WebPage","name":"Some Page"}
    </script>
    '''
    assert _extract_body_from_jsonld(html) == ""


def test_returns_empty_when_no_jsonld_block() -> None:
    html = "<html><body><p>Plain HTML, no JSON-LD.</p></body></html>"
    assert _extract_body_from_jsonld(html) == ""


def test_handles_malformed_jsonld_gracefully() -> None:
    """A broken JSON-LD block must not crash the extractor."""
    html = '''
    <script type="application/ld+json">
    {this is not json
    </script>
    <script type="application/ld+json">
    {"@type":"NewsArticle","articleBody":"Recovered from second block."}
    </script>
    '''
    out = _extract_body_from_jsonld(html)
    assert "Recovered from second block" in out


def test_handles_unicode_arabic_hebrew() -> None:
    """Article bodies are mixed-script in this pipeline."""
    html = '''
    <script type="application/ld+json">
    {"@type":"NewsArticle","articleBody":"גורמים דיפלומטים אמרו לרשת"}
    </script>
    '''
    out = _extract_body_from_jsonld(html)
    assert "גורמים" in out


def test_modern_selectors_in_fallback_list() -> None:
    """Belt-and-braces: even if JSON-LD is removed in a future Ynet rebuild,
    the fallback list must include `.ArticleBodyComponent` so we don't
    silently regress to 0-char bodies."""
    from crime_pipeline.scrapers.ynet import _BODY_SELECTORS
    selectors_str = " ".join(_BODY_SELECTORS)
    assert "ArticleBodyComponent" in selectors_str
