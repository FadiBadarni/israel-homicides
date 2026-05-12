"use client";

import type { Locality } from "@/lib/api";
import { ISRAEL_OUTLINE_LATLNG, MAP_HEIGHT, MAP_WIDTH, projectLatLng } from "@/lib/project";

interface IsraelMapProps {
  localities: Locality[];
  selectedCity: string | null;
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
  return Math.min(14, 3 + 2.5 * Math.sqrt(count));
}

function ringRadius(count: number): number {
  return Math.min(28, 8 + 4 * Math.sqrt(count));
}

export function IsraelMap({ localities, selectedCity, onSelect }: IsraelMapProps) {
  const outlinePoints = ISRAEL_OUTLINE_LATLNG
    .map(([lat, lng]) => projectLatLng(lat, lng))
    .map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
      className="w-full h-full"
      preserveAspectRatio="xMidYMid meet"
      data-testid="israel-map"
    >
      <polygon
        points={outlinePoints}
        fill="#ece6db"
        stroke="#2c2a26"
        strokeWidth={1}
        strokeLinejoin="round"
      />
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
              strokeWidth={selected ? 1.5 : 0.5}
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
