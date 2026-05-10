"""Perceptual + cryptographic image hashing.

Sync (CPU-bound) — runs via `asyncio.to_thread` in the pipeline.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Optional


def compute_sha256(image_bytes: bytes) -> str:
    """Byte-exact dedup key."""
    return hashlib.sha256(image_bytes).hexdigest()


def compute_phash(image_bytes: bytes) -> Optional[str]:
    """64-bit perceptual hash (DCT-based). Returns 16-hex string or None."""
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        return None
    try:
        img = Image.open(BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        ph = imagehash.phash(img, hash_size=8)
        return str(ph)
    except Exception:
        return None


def hamming_distance(phash_a: Optional[str], phash_b: Optional[str]) -> int:
    """Hamming distance between two pHash hex strings. 999 if either is None."""
    if not phash_a or not phash_b or len(phash_a) != len(phash_b):
        return 999
    try:
        a = int(phash_a, 16)
        b = int(phash_b, 16)
        return bin(a ^ b).count("1")
    except ValueError:
        return 999


def get_image_dims(image_bytes: bytes) -> tuple[Optional[int], Optional[int]]:
    """(width, height) or (None, None)."""
    try:
        from PIL import Image
    except ImportError:
        return None, None
    try:
        img = Image.open(BytesIO(image_bytes))
        return img.width, img.height
    except Exception:
        return None, None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
