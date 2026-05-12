"use client";

import { useEffect, useRef } from "react";
import maplibregl, { Map } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildMemorialStyle } from "@/lib/map-style";
import type { MemorialResponse } from "@/lib/api";

interface MemorialMapProps {
  memorial: MemorialResponse;
}

// Israel + West Bank + Gaza + Golan bounding box
const INITIAL_BOUNDS: [[number, number], [number, number]] = [
  [34.2, 29.5], // SW
  [35.9, 33.5], // NE
];

const TILES_URL = "/tiles/israel.pmtiles";

function pulseWeight(mostRecentIncidentDate: string | null): number {
  if (!mostRecentIncidentDate) return 0;
  const incident = new Date(mostRecentIncidentDate).getTime();
  if (isNaN(incident)) return 0;
  const days = (Date.now() - incident) / (1000 * 60 * 60 * 24);
  return Math.max(0, 1 - days / 30);
}

export function MemorialMap({ memorial }: MemorialMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildMemorialStyle(window.location.origin + TILES_URL),
      bounds: INITIAL_BOUNDS,
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
      maxPitch: 0,
      dragRotate: false,
    });

    mapRef.current = map;

    map.on("load", () => {
      const features = memorial.localities.map((loc) => ({
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [loc.lng, loc.lat] },
        properties: {
          city: loc.city,
          death_count: loc.death_count,
          pulse_weight: pulseWeight(loc.most_recent_incident_date),
        },
      }));

      map.addSource("localities", {
        type: "geojson",
        data: { type: "FeatureCollection", features },
      });

      // Static inner dot — radius scales with sqrt(death_count)
      map.addLayer({
        id: "locality-dot",
        type: "circle",
        source: "localities",
        paint: {
          "circle-color": "#8b2a1f",
          "circle-radius": [
            "min",
            14,
            ["+", 3, ["*", 2.5, ["sqrt", ["get", "death_count"]]]],
          ],
          "circle-stroke-width": 0.5,
          "circle-stroke-color": "#5a1b13",
        },
      });

      // Pulse ring — outer circle whose opacity oscillates
      map.addLayer({
        id: "locality-pulse",
        type: "circle",
        source: "localities",
        paint: {
          "circle-color": "transparent",
          "circle-stroke-color": "#8b2a1f",
          "circle-stroke-width": 2,
          "circle-stroke-opacity": 0,
          "circle-radius": [
            "min",
            28,
            ["+", 8, ["*", 4, ["sqrt", ["get", "death_count"]]]],
          ],
        },
      }, "locality-dot");  // insert beneath the solid dot

      let raf = 0;
      const tick = () => {
        const t = performance.now() / 1000;
        const sine = (Math.sin(t * 1.8) + 1) / 2; // 0..1 at ~0.3 Hz
        map.setPaintProperty("locality-pulse", "circle-stroke-opacity", [
          "*",
          sine * 0.45,
          ["get", "pulse_weight"],
        ]);
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);

      map.once("remove", () => cancelAnimationFrame(raf));
    });

    return () => {
      maplibregl.removeProtocol("pmtiles");
      map.remove();
      mapRef.current = null;
    };
  }, [memorial]);

  return <div ref={containerRef} className="w-full h-screen" />;
}
