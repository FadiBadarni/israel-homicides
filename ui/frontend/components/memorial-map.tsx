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

    return () => {
      maplibregl.removeProtocol("pmtiles");
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // memorial prop is unused here; consumed by the dot layer in a later task
  void memorial;

  return <div ref={containerRef} className="w-full h-screen" />;
}
