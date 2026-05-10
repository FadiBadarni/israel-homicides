"""
Deterministic hashing utilities used throughout the pipeline.
"""
from __future__ import annotations

import hashlib


def url_hash(url: str) -> str:
    """
    Return a 16-character hex digest of the URL, suitable for use as a
    short stable identifier (e.g. dedup graph node keys).
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def text_hash(text: str) -> str:
    """
    Return the full 64-character SHA-256 hex digest of *text*.

    Used to detect exact-duplicate article bodies before triggering the
    more expensive embedding-based similarity search.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(value: str, length: int = 8) -> str:
    """
    Return a *length*-character hex prefix of the SHA-256 digest of *value*.

    Useful for generating readable short IDs in output filenames.
    """
    if not 1 <= length <= 64:
        raise ValueError(f"length must be between 1 and 64, got {length}")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
