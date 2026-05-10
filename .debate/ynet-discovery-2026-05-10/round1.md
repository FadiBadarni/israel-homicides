# Debate: Ynet Discovery Strategy — Round 1: Opening Positions

**Constraints:** Full history (2020+), zero-auth, no JS execution, Hebrew queries

---

## Participant A — Gemini CLI
**Position: Google News RSS (Option 1)**

Advocates for Google News RSS as the only zero-auth solution that guarantees indexing depth and query precision for a 2020–present historical requirement.

**Key Arguments:**
- Superior indexing with `after:`/`before:` date operators — surgical precision over Ynet's massive archive
- `ceid=IL:he` taps Google's Israeli news index — Hebrew morphological variations handled correctly
- Structured RSS XML via httpx is more stable than HTML scraping; redirect follow is deterministic

**Biggest Risk:** Rate limiting (HTTP 429) — Google News sensitive to automated traffic from cloud IPs; CAPTCHAs impassable without JS

---

## Participant B — Codex CLI (GPT-5.5)
**Position: Google News RSS with date-window sharding (Option 1)**

Agrees Google News RSS is strongest, adds the key implementation detail of recursive date-window sharding for dense date ranges.

**Key Arguments:**
- Best fit for arbitrary Hebrew queries — handles tokenization and `site:ynet.co.il` better than any alternative
- Date ranges controllable by construction — split into monthly/weekly windows, dedupe canonical URLs
- Redirect URLs are an implementation detail — cheap `httpx` follow + host validation, more deterministic than HTML scraping

**Biggest Risk:** Recall loss from Google's result caps in dense windows — mitigated by recursive sharding when results approach the cap

---

## Participant C — Claude Sonnet (Agent)
**Position: Hybrid — Google News RSS primary + Ynet RSS supplement (Option 3)**

Advocates for the hybrid as strongest overall, citing the 6–48h crawl lag gap that Google News has for freshly published articles.

**Key Arguments:**
- Google News RSS is the only viable historical discovery path — after:/before: operators + site: filter unmatched by any zero-auth alternative
- Ynet RSS closes the recency gap — 6–48h crawl lag in Google News means recent events missed; StoryRss2.xml reflects last 30 items near-real-time
- Redirect resolution is tractable — existing `http_retry` decorator handles it; no bespoke logic needed

**Biggest Risk:** Google rate-limits or returns silent empty sets for non-Latin queries — mitigation: 1.5s inter-request delay + treat empty result as soft failure, retry with backoff, fallback to Ynet RSS

---

## Participant D — Claude Opus (Host)
**Position: Google News RSS with date-window sharding (Option 1)**

The Hybrid's Ynet RSS supplement adds complexity for minimal gain. Google News typically indexes Ynet within 2–6 hours; the recency gap is only relevant for same-day pipeline runs. The implementation cost of maintaining two discovery paths, deduplicating across them, and handling their different failure modes outweighs the marginal recency benefit.

**Key Arguments:**
- Single code path is more maintainable — one set of failure modes, one retry strategy, one URL normalizer
- Date sharding collapses the result cap problem — chunking by month yields ~50 results/chunk; pipeline can issue multiple calls transparently
- Google News Hebrew support is genuinely superior — Bing's non-English indexing lags and Ynet RSS has no query filter; Google is the only viable engine for historical Hebrew search

**Biggest Risk:** Google rate-limiting and silent empty responses — mitigated by 2s+ delay, exponential backoff, and treating an unexpectedly empty result as a retryable error
