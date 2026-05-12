"use client";

import type { Locality } from "@/lib/api";
import {
  ISRAEL_OUTLINE_LATLNG,
  MAP_HEIGHT,
  MAP_WIDTH,
  clusterGeometry,
  findClusters,
  projectLatLng,
  projectRing,
} from "@/lib/project";

interface IsraelMapProps {
  localities: Locality[];
  selectedCity: string | null;
  cityPolygons?: Record<string, [number, number][]>;
  onSelect: (city: string) => void;
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
          const ring = cityPolygons[loc.city];
          if (!ring) return null;
          const points = projectRing(ring);
          const selected = loc.city === selectedCity;
          return (
            <polygon
              key={`poly-${loc.city}`}
              points={points}
              fill={selected ? "#e0d4bf" : "#e3d8c5"}
              stroke="#b9ac96"
              strokeWidth={0.4}
              strokeDasharray={selected ? "0" : "1.5 1.5"}
              opacity={selected ? 0.95 : 0.7}
              pointerEvents="none"
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
