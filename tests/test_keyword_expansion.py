"""Gap 3 — Arabic keyword expansion.

The Jan 2026 truth investigation showed that several murder cases were
covered on Arab48 under verbs we weren't searching for. The fix adds
four Arabic keywords without dropping the existing four:

    قتل      — bare killing verb (broader than مقتل)
    تصفية    — "liquidation", gangland framing
    أردى     — "shot dead"
    جثة      — "body" — used in body-found articles

Hebrew presets are unchanged. We assert the literal keyword list in
__main__.py so an accidental rename / removal triggers the test.
"""
from __future__ import annotations

import inspect

from crime_pipeline import __main__ as cli_module


def test_arabic_keywords_include_expansion() -> None:
    """Every expanded Arabic keyword must appear in __main__.py source.
    The presets are defined inside the keyword_mode branch, so we
    inspect the source rather than trying to import the local list."""
    src = inspect.getsource(cli_module)
    # Original 4
    for kw in ("جريمة قتل", "مقتل", "إطلاق نار", "طعن"):
        assert kw in src, f"original Arabic keyword removed: {kw!r}"
    # New 4
    for kw in ("قتل", "تصفية", "أردى", "جثة"):
        assert kw in src, f"expansion Arabic keyword missing: {kw!r}"


def test_hebrew_keywords_unchanged() -> None:
    """The Hebrew preset must remain untouched — the Jan 2026 data
    showed Hebrew recall was already strong; only Arabic needed
    expansion."""
    src = inspect.getsource(cli_module)
    for kw in ("רצח", "נרצח", "ירי", "דקירה"):
        assert kw in src, f"Hebrew keyword regression: {kw!r}"
