"""
Tenacity-based retry decorators for HTTP and LLM calls.
"""
from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# ---------------------------------------------------------------------------
# HTTP retry — covers transient network errors and server-side 5xx responses
# ---------------------------------------------------------------------------

http_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
    reraise=True,
)

# ---------------------------------------------------------------------------
# LLM retry — covers anthropic rate-limit / transient API errors
# ---------------------------------------------------------------------------

try:
    import anthropic

    _llm_retry_exceptions: tuple[type[Exception], ...] = (
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.InternalServerError,
    )
except ImportError:
    # anthropic not installed yet — fall back to a broad Exception type
    _llm_retry_exceptions = (Exception,)

llm_retry = retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type(_llm_retry_exceptions),
    reraise=True,
)
