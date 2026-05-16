"""Decontaminate cluster-polluted canonical cases.

A canonical case is polluted when dedup over-merged unrelated victims
into one cluster. The symptoms:
  - 10+ sources for what should be 1-3 sources
  - Aliases naming completely different people
  - Media items dated months/years from the incident
  - 99 such cases identified across the dataset (worst: 334 sources)

Strategy (surgical, no full rebuild):
  1. Compute the case's primary-name token set (across all scripts)
  2. For each source URL, fetch raw_articles.article_text and check if
     the primary victim is actually mentioned (full name OR distinctive
     surname). Drop articles that don't.
  3. Drop aliases that share no substantial token with the primary.
  4. Drop media items whose source_article_urls were all dropped.
  5. Re-run media.finalize on the trusted candidate set.

Idempotent. Reports per-case before/after counts. Always commits per
case so a crash doesn't lose progress.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)
sys.path.insert(0, str(_ROOT))

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from crime_pipeline.config import Settings
from crime_pipeline.media.classifier import ArticleContext
from crime_pipeline.media.models import MediaCandidate
from crime_pipeline.media.pipeline import MediaPipeline, MEDIA_HARVEST_VERSION
from crime_pipeline.media.settings import MediaSettings
from crime_pipeline.models import CanonicalCase, RawArticle
from crime_pipeline.storage import db as db_module
from crime_pipeline.storage.db import init_db


MIN_TOKEN_LEN = 3   # minimum char length to count as a "substantial token"
MIN_SURNAME_LEN = 4 # surname must be this long for stand-alone match


def primary_tokens(case: dict) -> set[str]:
    """Multi-char tokens from all primary name fields (any script)."""
    out: set[str] = set()
    for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        for t in (case.get(k) or "").split():
            if len(t) >= MIN_TOKEN_LEN:
                out.add(t)
    return out


def primary_surnames(case: dict) -> set[str]:
    """Last token from each primary name field (likely surname)."""
    out: set[str] = set()
    for k in ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en"):
        toks = (case.get(k) or "").split()
        if toks and len(toks[-1]) >= MIN_SURNAME_LEN:
            out.add(toks[-1])
    return out


def alias_shares_token(alias: str, tokens: set[str], surnames: set[str]) -> bool:
    """True if alias shares a SURNAME (last-token) match with primary.

    Common given names (محمد/أحمد/علي/Mohammed/Ahmed/יוסף/مoshe...) cause
    false positives if we accept any shared token: alias "محمد قاسم" would
    pass against primary "محمد إبراهيم زعرورة" purely on shared "محمد".
    Require the surname (last token, ≥4 chars) of the alias to match a
    primary surname — that's the disambiguating signal across families.
    """
    if not alias:
        return False
    toks = alias.split()
    if not toks:
        return False
    # Strong match: full alias is the primary or shares the SURNAME token
    last = toks[-1]
    if len(last) >= MIN_SURNAME_LEN and last in surnames:
        return True
    # Allow short single-token aliases that match a primary surname exactly
    if len(toks) == 1 and len(last) >= MIN_SURNAME_LEN and last in surnames:
        return True
    return False


def article_mentions_victim(text: str, full_names: list[str], surnames: set[str]) -> bool:
    """Does the article body mention the primary victim?"""
    if not text:
        return False
    # Full name match (strongest signal)
    for n in full_names:
        if n and n in text:
            return True
    # Surname-only match (more permissive; risk of false positive on
    # very common surnames). MIN_SURNAME_LEN of 4 helps; common Arabic
    # surnames like "خطيب" (5 chars) still pass but a 3-char surname
    # like "سعد" wouldn't.
    for s in surnames:
        if s in text:
            return True
    return False


async def decontaminate_one(
    row: CanonicalCase,
    media_pipeline: MediaPipeline,
) -> dict:
    """Returns stats dict: keys = {sources_before, sources_after,
    aliases_before, aliases_after, media_before, media_after, changed}.
    """
    cj = dict(row.case_json or {})
    full_names = [
        cj.get(k) for k in
        ("victim_name", "victim_name_ar", "victim_name_he", "victim_name_en")
        if cj.get(k)
    ]
    tokens = primary_tokens(cj)
    surnames = primary_surnames(cj)

    sources_before = len(cj.get("sources") or [])
    aliases_before = len(cj.get("aliases") or [])
    media_before = len(cj.get("media") or []) + len(cj.get("media_evidence") or [])

    # Filter aliases by surname-match with primary
    kept_aliases = [
        a for a in (cj.get("aliases") or [])
        if alias_shares_token(a, tokens, surnames)
    ]
    cj["aliases"] = kept_aliases

    # Filter sources by article mentions
    src_urls = [s.get("url") for s in (cj.get("sources") or []) if s.get("url")]
    with db_module.SessionLocal() as sess:
        arts = list(sess.scalars(select(RawArticle).where(RawArticle.url.in_(src_urls))))
    art_by_url = {a.url: a for a in arts}

    trusted_urls: set[str] = set()
    for u in src_urls:
        a = art_by_url.get(u)
        if a is None:
            # No DB row for this URL — keep it (we can't validate)
            trusted_urls.add(u)
            continue
        txt = a.article_text or ""
        if not txt:
            # No text → keep (can't validate either way)
            trusted_urls.add(u)
            continue
        if article_mentions_victim(txt, full_names, surnames):
            trusted_urls.add(u)

    # Filter the sources list to trusted URLs
    cj["sources"] = [s for s in (cj.get("sources") or []) if s.get("url") in trusted_urls]
    sources_after = len(cj["sources"])

    # Re-build media from trusted source articles using cached harvest
    ctx = ArticleContext(
        article_url=next(iter(trusted_urls), ""),
        victim_names=full_names,
        suspect_names=[cj.get("suspect_name")] if cj.get("suspect_name") else [],
        city_names=[c for c in (cj.get("city"),) if c],
    )
    media_pipeline.classifier.reset_case_budget()
    all_cands: list[MediaCandidate] = []
    for u in trusted_urls:
        a = art_by_url.get(u)
        if a is None or a.media_harvest_json is None: continue
        if a.media_harvest_version != MEDIA_HARVEST_VERSION: continue
        try:
            all_cands.extend(MediaCandidate(**d) for d in a.media_harvest_json)
        except Exception:
            pass

    media_canon, evidence_canon = await media_pipeline.finalize(all_cands, ctx) if all_cands else ([], [])
    cj["media"] = [m.model_dump(mode="json") for m in media_canon]
    cj["media_evidence"] = [m.model_dump(mode="json") for m in evidence_canon]
    media_after = len(cj["media"]) + len(cj["media_evidence"])

    aliases_after = len(cj["aliases"])
    changed = (
        sources_before != sources_after
        or aliases_before != aliases_after
        or media_before != media_after
    )

    if changed:
        with db_module.SessionLocal() as sess:
            live = sess.get(CanonicalCase, row.id)
            if live is not None:
                live.case_json = cj
                # Also update sources_merged column (mirror)
                live.sources_merged = [s.get("url") for s in cj.get("sources", []) if s.get("url")]
                flag_modified(live, "case_json")
                sess.commit()

    return {
        "sources_before": sources_before, "sources_after": sources_after,
        "aliases_before": aliases_before, "aliases_after": aliases_after,
        "media_before": media_before, "media_after": media_after,
        "changed": changed,
    }


async def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    init_db(settings.db_path)
    assert db_module.SessionLocal is not None

    with db_module.SessionLocal() as sess:
        rows = list(sess.scalars(select(CanonicalCase).where(CanonicalCase.pipeline_run_id.like("canonical_%"))))
    print(f"scanning {len(rows)} canonical_* rows...")

    media_pipeline = MediaPipeline(MediaSettings())

    changed_count = 0
    total_dropped_sources = 0
    total_dropped_aliases = 0
    total_dropped_media = 0
    for i, r in enumerate(rows, 1):
        try:
            stats = await decontaminate_one(r, media_pipeline)
        except Exception as e:
            print(f"  [{i:4d}/{len(rows)}] ERROR: {e}")
            continue
        if stats["changed"]:
            changed_count += 1
            d_src = stats["sources_before"] - stats["sources_after"]
            d_ali = stats["aliases_before"] - stats["aliases_after"]
            d_med = stats["media_before"] - stats["media_after"]
            total_dropped_sources += max(0, d_src)
            total_dropped_aliases += max(0, d_ali)
            total_dropped_media += max(0, d_med)
            cj = r.case_json or {}
            name = (cj.get("victim_name_ar") or cj.get("victim_name") or "?")[:30]
            if d_src >= 5 or d_ali >= 5:
                print(
                    f"  [{i:4d}/{len(rows)}] {name}  "
                    f"src {stats['sources_before']}→{stats['sources_after']} (-{d_src})  "
                    f"aliases {stats['aliases_before']}→{stats['aliases_after']} (-{d_ali})  "
                    f"media {stats['media_before']}→{stats['media_after']} (-{d_med})"
                )

    print()
    print("=== Summary ===")
    print(f"  cases scanned:          {len(rows)}")
    print(f"  cases changed:          {changed_count}")
    print(f"  total sources dropped:  {total_dropped_sources}")
    print(f"  total aliases dropped:  {total_dropped_aliases}")
    print(f"  total media dropped:    {total_dropped_media}")


if __name__ == "__main__":
    asyncio.run(main())
