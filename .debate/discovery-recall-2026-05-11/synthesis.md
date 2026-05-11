# Debate Synthesis: Discovery Recall Gap for Body-Only Query Terms
**Date:** 2026-05-11  
**Topic:** How should the pipeline solve the case where victim name appears only in article body, not title (e.g. "בכר יאסין" — 3 Israel Hayom articles, zero RSS hits)

---

## Positions

### Gemini
**Verdict: D + B (targeted sitemap)**  
Two-pass model: initial RSS query with victim name; if zero results, trigger a *selective* sitemap crawl — fetch only recent URLs from `googlenews.xml`, download those articles, local full-text search. Rejects E on the grounds that incident-descriptor queries create a "massive downstream disambiguation problem" (location+crime-type queries match hundreds of unrelated cases). Rejects C (paid API cost). Calls A "simple query expansion" — insufficient on its own.

### Codex
**Verdict: B + D + E (A sparingly, C as last resort)**  
Root cause is that RSS is the wrong primitive for body-only terms — no RSS querying fixes it durably. Sitemap crawl + local full-text filtering (B) is the structural fix. E should be the default query philosophy: victim names should *not* be primary queries because Israeli crime headlines rarely include names (titles say "doctor murdered his brother", "man shot in village"). D for efficient escalation — first pass focused, second pass broader. A is useful as controlled expansion but not the core fix. C only for unresolved paid backfill.

### Claude Sonnet
**Verdict: E + D + A**  
Incident descriptors (location + crime type + year) as primary queries — this is what the pipeline should have been doing all along. Multi-query expansion (A) to generate variants. Two-pass (D) for thin-result cases. Does not include sitemap crawl (B) as a core requirement, seeing it as operationally expensive.

### Claude Opus
**Verdict: E + D (reject A, B, C)**  
Clearest rejection of the current approach: victim names are the wrong primary query because Arabic names almost never appear in Hebrew newspaper headlines. Fix query generation to use incident descriptors as the default. Two-pass when first-pass returns < threshold. Explicitly rejects B (sitemap crawl) as high operational cost for marginal gain once E is implemented correctly.

---

## Consensus Map

| Solution | Gemini | Codex | Sonnet | Opus | Score |
|----------|--------|-------|--------|------|-------|
| A — Multi-query expansion | No | Sparingly | Yes | No | 1.5/4 |
| B — Sitemap crawl | Yes (targeted) | Yes (core) | No | No | 2/4 |
| C — Paid API | No | Last resort | No | No | 0/4 |
| D — Two-pass discovery | Yes | Yes | Yes | Yes | **4/4** |
| E — Incident descriptors as primary | No | Yes | Yes | Yes | 3/4 |

---

## Synthesis

**Strong consensus (4/4): D — Two-pass discovery**  
All providers agree: first pass with current query, second pass triggered when results are below a threshold (suggested: < 3 articles). The second pass uses broader location + crime-type queries.

**Majority consensus (3/4): E — Redesign query generation**  
The pipeline currently uses victim names as primary queries because that's what the user provides. Three of four providers argue this is architecturally wrong for Arabic-sector victims: Hebrew news headlines virtually never include Arabic names. Switching primary queries to incident descriptors (city + crime type + year) catches the "doctor killed his brother in Arraba 2026" class of articles that RSS currently misses. The one dissent (Gemini) is a valid concern about disambiguation — location+crime queries can match many unrelated cases — but this is mitigated by date windowing and the LLM extraction stage filtering non-matching articles.

**Minority (2/4): B — Targeted sitemap crawl**  
The most thorough fix but operationally expensive. Gemini and Codex support it; Sonnet and Opus reject it as over-engineering once E is implemented. **Recommendation: defer B as a Phase 2 enhancement.** If E+D still misses cases, implement targeted sitemap crawl (recent-only URLs, no full index).

---

## Recommended Implementation

### Phase 1 (implement now): E + D

**E — Query redesign in `pipeline.py`:**  
When building queries for `discover()` calls, default to incident descriptors:
```
{city} רצח {year}          # "Arraba murder 2026" 
{city} ירי {year}
{city} דקירה {year}
```
Keep victim name as an *additional* query variant, not the primary.  
For Arabic sources (Arab48, Panet), use Arabic equivalents.

**D — Two-pass logic in `pipeline.py` (or per-scraper `discover()`):**  
If first-pass returns < 3 unique articles, trigger second pass with location + crime-type descriptors.  
Merge and dedup against first-pass results.

### Phase 2 (future): B — Targeted sitemap

If Phase 1 still misses body-only cases: fetch `sitemap.xml` or Google News sitemap from Israel Hayom / Ynet for the relevant date window (not the full index), fetch those articles, local full-text match on victim name + location.

---

## Winner

**E + D** — Query redesign + two-pass discovery.  
Supported by 3 of 4 providers, lowest implementation cost, highest ratio of recall gain to engineering effort. Does not require per-source sitemap knowledge or local full-text indexing infrastructure. Dedup + LLM extraction stage handles the broader query noise.
