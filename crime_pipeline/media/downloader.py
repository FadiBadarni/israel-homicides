"""Async image downloader with size guard, format sniff, redirect tracking.

Per-URL hash cache keyed on sha256(url) → cached bytes path. Avoids
re-downloading the same image when it appears in multiple articles
(addresses debate-fix #1).
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

import httpx
import structlog

from crime_pipeline.media.hashing import (
    compute_phash, compute_sha256, get_image_dims,
)
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.settings import MediaSettings

log = structlog.get_logger()


class MediaDownloader:
    """Bandwidth-bounded async image fetcher with on-disk cache."""

    def __init__(self, settings: MediaSettings) -> None:
        self.settings = settings
        self.cache_dir = Path(settings.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._url_cache: dict[str, Path] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def fetch_many(
        self,
        candidates: list[MediaCandidate],
        client: Optional[httpx.AsyncClient] = None,
    ) -> list[MediaCandidate]:
        """Download all candidates concurrently (bounded by per-host conn limit)."""
        owns_client = client is None
        if owns_client:
            limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
            client = httpx.AsyncClient(
                http2=True, follow_redirects=True,
                timeout=self.settings.download_timeout_s,
                limits=limits,
            )
        try:
            tasks = [self._fetch_one(c, client) for c in candidates]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            return list(results)
        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_one(
        self, cand: MediaCandidate, client: httpx.AsyncClient
    ) -> MediaCandidate:
        url = cand.source_url
        url_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]

        # 1. Cache hit (cross-source same-URL dedup) — debate-fix #1
        if url_key in self._url_cache and self._url_cache[url_key].exists():
            cached = self._url_cache[url_key]
            try:
                data = cached.read_bytes()
                return self._populate_from_bytes(cand, data, str(cached), final_url=url)
            except Exception:
                pass  # fall through to refetch

        max_bytes = self.settings.max_image_size_mb * 1024 * 1024
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code == 200:
                    cl = resp.headers.get("content-length")
                    if cl and int(cl) > max_bytes:
                        cand.download_status = "too_large"
                        cand.error_message = f"content-length={cl} > {max_bytes}"
                        return cand
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            cand.download_status = "too_large"
                            cand.error_message = f"streamed {total} bytes > {max_bytes}"
                            return cand
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    cache_path = self.cache_dir / f"{url_key}.bin"
                    try:
                        cache_path.write_bytes(data)
                        self._url_cache[url_key] = cache_path
                    except Exception:
                        pass
                    return self._populate_from_bytes(
                        cand, data, str(cache_path), final_url=str(resp.url),
                    )
                if resp.status_code in (403, 404):
                    cand.download_status = "http_error"
                    cand.error_message = f"HTTP {resp.status_code}"
                    return cand
                cand.download_status = "http_error"
                cand.error_message = f"HTTP {resp.status_code}"
                return cand
        except httpx.TimeoutException:
            cand.download_status = "timeout"
            return cand
        except Exception as e:
            cand.download_status = "blocked"
            cand.error_message = str(e)[:200]
            return cand

    def _populate_from_bytes(
        self,
        cand: MediaCandidate,
        data: bytes,
        bytes_ref: str,
        final_url: Optional[str] = None,
    ) -> MediaCandidate:
        cand.size_bytes = len(data)
        cand.sha256 = compute_sha256(data)
        cand.phash = compute_phash(data)
        if not cand.width or not cand.height:
            w, h = get_image_dims(data)
            cand.width = cand.width or w
            cand.height = cand.height or h
        cand.bytes_ref = bytes_ref
        cand.final_url = final_url
        cand.download_status = "ok" if cand.phash else "unsupported_format"
        return cand
