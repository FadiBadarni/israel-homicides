# Implementation Contract — Ynet discover() Rewrite
**Providers:** Gemini CLI, Codex CLI (GPT-5.5), Claude Sonnet (live research)
**Consensus:** 3/3 on all 6 points

---

## 1. Redirect Resolution (UNANIMOUS: batchexecute, 2-round-trip)

`follow_redirects=True` is DEAD. All providers confirmed the current mechanism (May 2026).

**Algorithm:**
1. Extract `base64_id` from URL path: last segment of `news.google.com/articles/{id}` or `news.google.com/rss/articles/{id}`
2. GET `https://news.google.com/articles/{base64_id}` with browser headers, `follow_redirects=False`
3. Detect consent/block: check for `consent.google.com`, `before you continue`, `unusual traffic`, `/sorry/` in response body — return `None` if found
4. Parse `c-wiz div[jscontroller][data-n-a-sg][data-n-a-ts]` — return `None` if absent
5. POST to `https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je`
6. Parse response: `json.loads(text.split("\n\n", 1)[1])[0][2]` → inner JSON → `[1]` is canonical URL
7. Validate: must start with `https://www.ynet.co.il/` or `https://ynet.co.il/`
8. On any failure → return `None`, log warning, skip item (never raise)

**Headers (same for both calls):**
```python
{
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://news.google.com/",
}
```

**POST payload f.req:**
```python
payload = ["Fbv4je", (
    '["garturlreq",'
    '[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
    '"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
    f'"{base64_id}",{timestamp},"{signature}"]'
)]
data = f"f.req={quote(json.dumps([[payload]]))}"
```

**No new dependencies** — uses only httpx + bs4 (already in project).

---

## 2. Window Arithmetic

```python
def _build_windows(date_from: str, date_to: str) -> list[tuple[datetime, datetime]]:
    start = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
    windows, cursor = [], start
    while cursor < end:
        nxt = min(cursor + timedelta(hours=48), end)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows
```

Google query format: `f"{query} site:ynet.co.il after:{start.strftime('%Y-%m-%d')} before:{end.strftime('%Y-%m-%d')}"`

RSS URL: `https://news.google.com/rss/search?q={encoded_q}&hl=he&gl=IL&ceid=IL:he`

---

## 3. Bisect Logic (iterative, NOT recursive)

- Threshold: `>= 90` items returned → bisect
- Max depth: `4`
- Min window: `3 hours`
- Below min or at max depth: process items as-is, log `gnews_window_saturated`

```python
queue = deque((start, end, 0) for start, end in initial_windows)
while queue:
    start, end, depth = queue.popleft()
    items = await _fetch_gnews_window(client, query, start, end)
    if len(items) >= 90 and depth < 4 and (end - start) > timedelta(hours=3):
        mid = start + (end - start) / 2
        queue.appendleft((mid, end, depth + 1))
        queue.appendleft((start, mid, depth + 1))
        continue
    # process items
```

---

## 4. Early Stop

Check `len(results) >= max_results` at THREE points:
1. Before issuing each RSS window request
2. Before resolving each Google redirect URL
3. After appending each DiscoveredUrl

Return `results[:max_results]` immediately when cap hit.

---

## 5. Ynet RSS Supplement

**Activation condition:**
```python
date_to_dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
if date_to_dt >= datetime.now(timezone.utc) - timedelta(hours=72):
    # fetch Ynet RSS
```

**URL:** `https://www.ynet.co.il/Integration/StoryRss2.xml`

**Keyword filter (AND match, no stemming):**
```python
def _matches_query(title: str, tags: str, query: str) -> bool:
    terms = [t.lower() for t in query.split() if t.strip()]
    haystack = (title + " " + tags).lower()
    return all(term in haystack for term in terms)
```

**PubDate:** CDATA-wrapped, timezone +0300 — use `email.utils.parsedate_to_datetime`

---

## 6. PubDate Parsing (handles both UTC and +0300)

```python
from email.utils import parsedate_to_datetime

def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None
```

---

## 7. Source Field

Both Google News and Ynet RSS items get `source = self.source_name` (= `"ynet"`) to maintain compatibility with the pipeline's dedup and merge stages. The origin (gnews vs ynet rss) is logged but not stored in `DiscoveredUrl`.

---

## 8. Error Contract

| Error | Action |
|---|---|
| Single redirect resolution fails | Log warning, skip item, continue |
| Google returns 429 | Stop issuing further windows, return partial results |
| Google returns 0 items (potential silent empty) | Log warning, continue to next window |
| Network down / timeout | Return whatever was collected so far (empty list if nothing yet) |
| Ynet RSS fetch fails | Log warning, skip supplement, return Google results only |
| ALL windows fail | Return `[]` |

Never raise from `discover()` to caller.
