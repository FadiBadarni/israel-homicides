"""Fetch municipal-boundary polygons for every city in data/gazetteer.json.

Source: OpenStreetMap via the Overpass API. Queries each city by Hebrew name
first, then Arabic, then English, then aliases. Skips cities without an
`admin_level=8` boundary relation (common for very small Bedouin villages).

Output: data/city_polygons.json and ui/frontend/public/city-polygons.json,
both keyed by name_en, with arrays of [lng, lat] point lists in WGS84.

The polygons are simplified via Douglas-Peucker (tolerance ~0.002°, roughly
220 m) so each city ends up as a few dozen points rather than hundreds.

This is a one-shot operator script. Re-run when the gazetteer changes.

Usage:
    python scripts/fetch_city_polygons.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import urllib.request
import urllib.error
import urllib.parse

# Ensure stdout can carry Hebrew/Arabic on Windows consoles (cp1252 default).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Overpass rejects requests without a real User-Agent (returns 406).
USER_AGENT = "israel-homicides-memorial/1.0 (https://github.com/FadiBadarni/crime)"

# Douglas-Peucker tolerance in degrees. 0.002° ≈ 220 m at this latitude.
SIMPLIFY_TOLERANCE = 0.002

# Polite delay between Overpass requests
REQUEST_DELAY_SECONDS = 1.5


def overpass_query(query: str, retries: int = 3, timeout: int = 30) -> dict[str, Any]:
    """POST a query to Overpass and return parsed JSON. Retries on transient errors."""
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OVERPASS_URL,
                data=data,
                method="POST",
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Overpass request failed after {retries} retries: {last_err}")


def build_query(name_key: str, name_value: str) -> str:
    """Overpass QL: find admin_level=8 relations matching the given name tag.

    Returns the relation's geometry (rings of nodes).
    """
    # Escape double-quotes in the name
    escaped = name_value.replace('"', '\\"')
    return f"""
[out:json][timeout:25];
relation["admin_level"="8"]["{name_key}"="{escaped}"];
out geom;
""".strip()


def extract_outer_ring(rel: dict[str, Any]) -> list[tuple[float, float]] | None:
    """From an Overpass relation, find the first 'outer' way and return its
    points as [(lng, lat), ...]. Closes the ring if it isn't already closed.

    Multi-polygon cities: we take only the outer ring of the first outer member.
    Good enough for the abstract civic aesthetic.
    """
    for member in rel.get("members", []):
        if member.get("type") != "way":
            continue
        if member.get("role") != "outer":
            continue
        geom = member.get("geometry", [])
        if not geom:
            continue
        pts = [(node["lon"], node["lat"]) for node in geom]
        if not pts:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts
    return None


def douglas_peucker(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker polyline simplification.

    Input is a closed ring: the first/last points are identical.
    Output preserves the closed property and never returns fewer than 4 points.
    """
    if len(points) < 4:
        return points

    def perp_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        ax, ay = a
        bx, by = b
        px, py = p
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        cx, cy = ax + t * dx, ay + t * dy
        return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

    # Operate on the open polyline (drop the duplicated closing point), then re-close at the end
    open_pts = points[:-1]
    keep = [False] * len(open_pts)
    keep[0] = True
    keep[-1] = True

    stack = [(0, len(open_pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        max_d = 0.0
        max_k = i
        for k in range(i + 1, j):
            d = perp_dist(open_pts[k], open_pts[i], open_pts[j])
            if d > max_d:
                max_d = d
                max_k = k
        if max_d > epsilon:
            keep[max_k] = True
            stack.append((i, max_k))
            stack.append((max_k, j))

    simplified = [p for p, k in zip(open_pts, keep) if k]
    if len(simplified) < 3:
        simplified = open_pts[: min(4, len(open_pts))]
    simplified.append(simplified[0])
    return simplified


def fetch_one_city(entry: dict[str, Any]) -> list[tuple[float, float]] | None:
    """Try several name fields in order; return the first non-empty polygon found."""
    name_en = entry.get("name_en", "")
    attempts: list[tuple[str, str]] = []
    if entry.get("name_he"):
        attempts.append(("name:he", entry["name_he"]))
    if entry.get("name_ar"):
        attempts.append(("name:ar", entry["name_ar"]))
    if name_en:
        attempts.append(("name:en", name_en))
    for alias in entry.get("aliases_he", []) or []:
        attempts.append(("name:he", alias))
    for alias in entry.get("aliases_ar", []) or []:
        attempts.append(("name:ar", alias))

    for tag, value in attempts:
        try:
            data = overpass_query(build_query(tag, value))
        except Exception as e:
            print(f"  Overpass error for {name_en} ({tag}={value}): {e}")
            continue
        elements = data.get("elements", [])
        for elem in elements:
            if elem.get("type") != "relation":
                continue
            ring = extract_outer_ring(elem)
            if ring and len(ring) >= 4:
                return ring
        time.sleep(REQUEST_DELAY_SECONDS)
    return None


def main() -> None:
    src = Path("data/gazetteer.json")
    out_data = Path("data/city_polygons.json")
    out_public = Path("ui/frontend/public/city-polygons.json")

    entries = json.loads(src.read_text(encoding="utf-8"))
    print(f"Fetching polygons for {len(entries)} cities...")

    polygons: dict[str, list[list[float]]] = {}
    missing: list[str] = []

    for entry in entries:
        name_en = entry.get("name_en", "")
        if not name_en:
            continue
        print(f"  -> {name_en}")
        ring = fetch_one_city(entry)
        if ring is None:
            missing.append(name_en)
            continue
        simplified = douglas_peucker(ring, SIMPLIFY_TOLERANCE)
        polygons[name_en] = [[lng, lat] for (lng, lat) in simplified]
        time.sleep(REQUEST_DELAY_SECONDS)

    out_data.parent.mkdir(parents=True, exist_ok=True)
    out_public.parent.mkdir(parents=True, exist_ok=True)
    payload = {"polygons": polygons}
    out_data.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(out_data, out_public)

    print(f"\nFetched {len(polygons)} polygons; {len(missing)} missing:")
    for name in missing:
        print(f"  - {name}")
    print(f"\nWrote {out_data} and {out_public}.")


if __name__ == "__main__":
    main()
