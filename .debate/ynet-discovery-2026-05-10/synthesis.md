# Debate Synthesis: Ynet Discovery Strategy
**Date:** 2026-05-10 | **Rounds:** 3 | **Participants:** Gemini CLI, Codex CLI (GPT-5.5), Claude Sonnet, Claude Opus

---

## Verdict: Hybrid — Google News RSS (primary) + Ynet RSS (72h trailing supplement)

**Consensus score:** 3/4 participants converged on Hybrid by Round 3.

---

## How the Debate Evolved

### Round 1 — Divergence
| Participant | Position |
|---|---|
| Gemini | Google News RSS only — superior Hebrew indexing, stable XML |
| Codex | Google News RSS + date-window sharding — recall over cap |
| Sonnet | Hybrid — Google News RSS + Ynet RSS for recency gap |
| Opus | Google News RSS only — Hybrid complexity not worth recency gain |

### Round 2 — Key Attacks
- **Gemini → Codex:** Monthly sharding is too coarse; 48h windows required given ~100 result cap
- **Codex → Opus:** "Google indexes Ynet in 2-6h" is an assumption — Hebrew crawl priority is lower; breaking crime stories can lag 8-24h
- **Sonnet → Codex:** Dense news cycles blow through per-window result cap even with sharding
- **Codex → Gemini:** Monthly sharding is cap-mitigation, not magic recall; sharding reduces, not eliminates, truncation risk

### Round 3 — Convergence
By Round 3, all participants adopted **48h windows** as the correct sharding granularity. Three of four (Gemini, Codex, Sonnet) converged on including Ynet RSS as a trailing-window supplement. The debate moved from "which approach" to "what granularity and when to activate the supplement."

---

## Winning Implementation Spec

### Primary: Google News RSS with 48h window sharding

```
URL: https://news.google.com/rss/search?q={url_encoded_query}+site:ynet.co.il&hl=he&gl=IL&ceid=IL:he
Date operators: after:YYYY-MM-DD before:YYYY-MM-DD (appended to q parameter)
Window size: 48h chunks (overlapping by 24h to prevent boundary drops)
Rate limit: 1 request per 10 seconds, exponential backoff on 429/403
Max results: stop issuing windows once max_results URLs collected (deduped)
```

**Window generation logic:**
1. Split `[date_from, date_to]` into 48h chunks
2. Issue one RSS fetch per chunk
3. Deduplicate canonical URLs across windows
4. Stop early once `max_results` reached

### Redirect Resolution
Google News RSS returns `news.google.com/rss/articles/...` URLs. Resolve with:
```python
resp = await client.get(google_url, follow_redirects=True)
canonical_url = str(resp.url)
# Validate: must be ynet.co.il/news/article/
```
If redirect hits consent/CAPTCHA page, skip and log — do not block the pipeline.

### Supplement: Ynet RSS (trailing 72h only)
```
URL: https://www.ynet.co.il/Integration/StoryRss2.xml
Activate when: date_to >= (now - 72h)  # i.e. pipeline run covers recent period
Rate limit: 1 request per 5 seconds
Filter: keyword match in title/description, then date filter
```
Rationale: Hebrew Google News indexing lag for Ynet crime coverage is empirically 8–24h. For pipeline runs covering recent events, the Ynet RSS closes this gap at near-zero implementation cost.

---

## Key Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Google rate-limit / 429 | High for dense runs | 10s inter-request delay + exponential backoff + treat empty result as soft failure |
| Google silent empty set (non-Latin query) | Medium | Verify with a test query on startup; treat 0 items as retryable error |
| Redirect hits consent/CAPTCHA | Low-medium | Skip + log; do not crash pipeline |
| Ynet RSS 30-item cap misses events in trailing window | Low | Accept — supplement captures breaking coverage; Google eventually indexes remainder |
| 48h window still truncates at ~100 results | Medium for dense news cycles | Recursive split: if shard returns >= 90 items, bisect into 24h windows |

---

## What Was Rejected and Why

| Option | Rejected because |
|---|---|
| Ynet RSS only | 30-item cap + no query filter = cannot satisfy full-history requirement |
| Google CSE JSON API | Requires API key (zero-auth constraint violated) |
| Bing News RSS | No participant championed it; Bing's Hebrew/Israeli news indexing significantly lags Google's |
| Monthly sharding | Too coarse — dense topics easily exceed ~100 result cap within a month |

---

## Dissent Log

**Opus (Round 1–2):** Argued the Hybrid adds unnecessary complexity since Google indexes Ynet within 2-6h. Overruled by Codex and Sonnet's point that Hebrew-language crawl priority is lower, and that for a crime pipeline (where arrest windows are operationally critical) the 8-24h lag is meaningful, not negligible.
