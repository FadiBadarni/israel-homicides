# Discover Phase — Probe Synthesis
**Providers:** Gemini CLI, Codex CLI (GPT-5.5), Claude Agent (live probe)

---

## 1. Google News RSS XML Structure

```xml
<item>
  <title>כותרת הכתבה - ynet.co.il</title>
  <link>https://news.google.com/rss/articles/CBMiWkFV...BASE64...?oc=5</link>
  <guid isPermaLink="false">CBMiWkFV...BASE64...</guid>
  <pubDate>Sun, 10 May 2026 13:49:02 GMT</pubDate>
  <description>&lt;a href="https://news.google.com/rss/articles/..."&gt;title&lt;/a&gt;
    &amp;nbsp;&lt;font color="#6f6f6f"&gt;ynet.co.il&lt;/font&gt;</description>
  <source url="https://www.ynet.co.il">ynet.co.il</source>
</item>
```

Key notes:
- `<link>` is ALWAYS a Google redirect URL — never a direct ynet.co.il URL
- `<guid>` is the raw base64 article ID with `isPermaLink="false"` — NOT a usable URL
- `<description>` contains only the same Google redirect URL — no canonical URL
- `<source url="https://www.ynet.co.il">` confirms the publisher
- Parsed pubDate is UTC (GMT), times normalized to 08:00:00 for date-bounded queries

## 2. Date Operators

Syntax: embed in `q` parameter
```
q=רצח+site:ynet.co.il+after:2026-01-01+before:2026-02-01
```
- `after:YYYY-MM-DD` — inclusive from that date
- `before:YYYY-MM-DD` — exclusive (up to but not including)
- Live probe confirmed: date-filtered query returned 100 items, all within Jan 2026

## 3. Pagination

- **No pagination support** — no `start=` or `num=` params
- Hard cap: **100 items per query**
- Strategy: iterate with 48h windows using `after:`/`before:` operators

## 4. CRITICAL — Google Redirect URL Resolution

⚠️ **Simple `follow_redirects=True` no longer reliably works.**

Google News changed redirect mechanism — URLs are base64-encoded protobuf IDs. 
Standard HTTP redirect chain often hits a consent/interstitial page.

**Known working approaches (2025/2026):**
1. **`gnewsdecoder` library** — purpose-built Python library for this exact problem
2. **batchexecute POST** to `https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je`
3. **Browser-like GET** with full headers + follow_redirects — sometimes works (unreliable)

**Recommended implementation approach:**
- Try option 3 first (cheap, no dependency)
- If result URL is not `ynet.co.il/news/article/`, try option 2
- Log `google_redirect_unresolved` and skip — don't crash pipeline

## 5. Rate Limits

- Safe: **10–20 RPM** (1 req per 3–6 seconds)
- Throttle point: ~60 RPM triggers 429
- Cloud IPs throttled more aggressively than residential
- **Recommended delay: 10s between Google News requests**

## 6. Language Parameters

Confirmed correct:
```
hl=he&gl=IL&ceid=IL:he
```

## 7. Ynet Native RSS Structure

```xml
<item>
  <title><![CDATA[Article title]]></title>
  <description><![CDATA[<div><a href='...'><img src='...'></a></div>Summary text]]></description>
  <link><![CDATA[https://www.ynet.co.il/news/article/ryomks0cbg]]></link>
  <pubDate><![CDATA[Sun, 10 May 2026 20:38:33 +0300]]></pubDate>
  <guid><![CDATA[https://www.ynet.co.il/news/article/ryomks0cbg]]></guid>
  <tags><![CDATA[tag1 , tag2]]></tags>
</item>
```

- `<link>` = direct canonical ynet.co.il URL (no redirect!)
- All fields in CDATA wrappers
- `pubDate` uses +0300 timezone (not UTC)
- `<tags>` provides Hebrew keyword hints useful for query filtering
- No date range control — rolling latest only (~30 items)

---

## Implementation Checklist for Develop Phase

- [ ] Parse Google News RSS with `xml.etree.ElementTree` or `feedparser`
- [ ] Generate 48h window chunks between date_from and date_to
- [ ] URL-encode Hebrew query terms with `urllib.parse.quote_plus`
- [ ] Resolve Google redirect URLs: try follow_redirects first, fallback to batchexecute POST
- [ ] Validate resolved URL matches `ynet.co.il/news/article/` pattern
- [ ] Parse pubDate from both sources (handle UTC vs +0300 offset)
- [ ] Ynet RSS: activate when date_to >= (now - 72h); keyword-filter by query terms in title+tags
- [ ] Dedup by canonical URL across Google News windows and Ynet RSS
- [ ] Rate limit: 10s between Google News requests, 5s for Ynet RSS
