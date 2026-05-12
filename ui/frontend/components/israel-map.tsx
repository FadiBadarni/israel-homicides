"use client";

import type { Locality } from "@/lib/api";
import {
  ISRAEL_OUTLINE_LATLNG,
  MAP_HEIGHT,
  MAP_WIDTH,
  clusterGeometry,
  findClusters,
  projectLatLng,
} from "@/lib/project";

interface IsraelMapProps {
  localities: Locality[];
  selectedCity: string | null;
  cityPolygons?: Record<string, [number, number][]>;
  onSelect: (city: string) => void;
}

interface ProjectedPolygon {
  points: string;
}

function pulseWeight(mostRecentIncidentDate: string | null): number {
  if (!mostRecentIncidentDate) return 0;
  const t = new Date(mostRecentIncidentDate).getTime();
  if (isNaN(t)) return 0;
  const days = (Date.now() - t) / 86_400_000;
  return Math.max(0, 1 - days / 30);
}

function dotRadius(count: number): number {
  return Math.min(16, 4 + 2.5 * Math.sqrt(count));
}

function ringRadius(count: number): number {
  return Math.min(30, 10 + 4 * Math.sqrt(count));
}

function polygonForLocality(
  loc: Locality,
  cityPolygons?: Record<string, [number, number][]>,
): ProjectedPolygon | null {
  if (!cityPolygons) return null;
  const ring =
    cityPolygons[loc.city] ||
    (loc.city_he ? cityPolygons[loc.city_he] : undefined) ||
    (loc.city_ar ? cityPolygons[loc.city_ar] : undefined);
  if (!ring || ring.length < 3) return null;

  const projected = ring.map(([lng, lat]) => projectLatLng(lat, lng));
  const minX = Math.min(...projected.map((p) => p.x));
  const maxX = Math.max(...projected.map((p) => p.x));
  const minY = Math.min(...projected.map((p) => p.y));
  const maxY = Math.max(...projected.map((p) => p.y));
  const width = maxX - minX;
  const height = maxY - minY;
  const maxSpan = Math.max(width, height);

  // Real municipal boundaries are often only a few pixels wide at this
  // full-country scale. Scale only the tiny rings around their own centroid
  // so they remain visible/clickable without shifting their location.
  const minVisibleSpan = 26;
  const scale = maxSpan > 0 && maxSpan < minVisibleSpan
    ? Math.min(minVisibleSpan / maxSpan, 6)
    : 1;
  const cx = projected.reduce((sum, p) => sum + p.x, 0) / projected.length;
  const cy = projected.reduce((sum, p) => sum + p.y, 0) / projected.length;
  const points = projected
    .map((p) => ({
      x: cx + (p.x - cx) * scale,
      y: cy + (p.y - cy) * scale,
    }))
    .map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  return { points };
}

export function IsraelMap({
  localities,
  selectedCity,
  cityPolygons,
  onSelect,
}: IsraelMapProps) {
  const outlinePoints = ISRAEL_OUTLINE_LATLNG
    .map(([lat, lng]) => projectLatLng(lat, lng))
    .map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  const clusters = findClusters(localities).filter((c) => c.length >= 2);

  return (
    <svg
      viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
      className="w-full h-full"
      preserveAspectRatio="xMidYMid meet"
      data-testid="israel-map"
    >
      {/* Soft halo behind the country shape */}
      <polygon
        points={outlinePoints}
        fill="none"
        stroke="#d8cfbf"
        strokeWidth={6}
        strokeLinejoin="round"
        opacity={0.55}
      />

      {/* Country shape */}
      <polygon
        points={outlinePoints}
        fill="#ece6db"
        stroke="#2c2a26"
        strokeWidth={1.25}
        strokeLinejoin="round"
      />

      {/* Affected-city polygons (faint, just under the dots) */}
      {cityPolygons &&
        localities.map((loc) => {
          const polygon = polygonForLocality(loc, cityPolygons);
          if (!polygon) return null;
          const selected = loc.city === selectedCity;
          return (
            <polygon
              key={`poly-${loc.city}`}
              points={polygon.points}
              fill={selected ? "#8b2a1f" : "#a13b2e"}
              stroke={selected ? "#45130d" : "#8b2a1f"}
              strokeWidth={selected ? 1.35 : 0.9}
              opacity={selected ? 0.48 : 0.28}
              className="cursor-pointer transition-opacity duration-150 hover:opacity-60"
              data-testid="city-polygon"
              onClick={(e) => {
                e.stopPropagation();
                onSelect(loc.city);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(loc.city);
                }
              }}
              role="button"
              tabIndex={0}
              aria-label={`Select ${loc.city}`}
            />
          );
        })}

      {/* Cluster rings (dashed, behind dots) */}
      {clusters.map((cluster, i) => {
        const { cx, cy, r } = clusterGeometry(cluster);
        return (
          <circle
            key={`cluster-${i}`}
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke="#9a8e7b"
            strokeWidth={0.75}
            strokeDasharray="2.5 2.5"
            opacity={0.7}
            pointerEvents="none"
          />
        );
      })}

      {/* Dots + pulse */}
      {localities.map((loc) => {
        const { x, y } = projectLatLng(loc.lat, loc.lng);
        const w = pulseWeight(loc.most_recent_incident_date);
        const selected = loc.city === selectedCity;
        return (
          <g key={loc.city}>
            {w > 0 && (
              <circle
                cx={x}
                cy={y}
                r={ringRadius(loc.death_count)}
                fill="none"
                stroke="#8b2a1f"
                strokeWidth={2}
                className="pulse-ring"
                style={{ "--w": w.toString() } as React.CSSProperties}
                pointerEvents="none"
              />
            )}
            <circle
              cx={x}
              cy={y}
              r={dotRadius(loc.death_count)}
              fill="#8b2a1f"
              stroke={selected ? "#2c2a26" : "#5a1b13"}
              strokeWidth={selected ? 1.5 : 0.75}
              className="cursor-pointer transition-[stroke-width] duration-150"
              onClick={(e) => {
                e.stopPropagation();
                onSelect(loc.city);
              }}
            />
          </g>
        );
      })}
    </svg>
  );
}
