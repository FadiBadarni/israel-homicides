"""Regression test: extractor MUST disable Gemini 2.5 thinking mode.

The bug: gemini-2.5-flash defaults thinking-mode ON. With it on, the model
spends most of ``max_output_tokens`` on hidden reasoning tokens and emits
a truncated JSON. ``json-repair`` then salvages the partial response into
a tiny valid dict (just ``victim_name``) which ``validate_extraction``
silently accepts as ``status=success``.

Symptom: every Arabic extraction looked successful but had only the
victim_name field populated. This regression test ensures thinking stays
disabled.
"""
from __future__ import annotations

import inspect

from crime_pipeline.extraction.extractor import ArticleExtractor


def test_base_config_disables_thinking_via_thinking_budget_zero() -> None:
    """The extractor's GenerateContentConfig must include thinking_budget=0.

    We assert via source inspection because the config is built inside the
    extract() coroutine — building it requires a real Gemini client. Source-
    level assertion catches any future regression where someone removes the
    thinking_config block.
    """
    src = inspect.getsource(ArticleExtractor.extract)
    assert "thinking_config" in src, (
        "extractor.extract() must pass thinking_config to disable Gemini "
        "2.5 thinking mode (otherwise extractions silently truncate)."
    )
    assert "thinking_budget=0" in src, (
        "thinking_budget MUST be 0; any positive value re-introduces the "
        "silent-truncation bug."
    )


def test_extractor_warns_on_max_tokens_truncation() -> None:
    """A defensive log warning fires when finish_reason is MAX_TOKENS so
    silent truncation can't recur unnoticed."""
    src = inspect.getsource(ArticleExtractor.extract)
    assert "extraction_truncated_at_max_tokens" in src, (
        "extractor must log a warning when finish_reason indicates the "
        "response was cut off."
    )
    assert "MAX_TOKENS" in src, (
        "extractor must check finish_reason for MAX_TOKENS to surface "
        "silent truncation."
    )
