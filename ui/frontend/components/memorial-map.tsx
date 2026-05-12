"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { Map, MapLayerMouseEvent } from "maplibre-gl";
import { Protocol } from "pmtiles";
import "maplibre-gl/dist/maplibre-gl.css";
import { buildMemorialStyle } from "@/lib/map-style";
import type { Locality, MemorialResponse } from "@/lib/api";
import { BloomCard } from "./bloom-card";
import { DeathCount } from "./death-count";
import { YearScrubber } from "./year-scrubber";

interface MemorialMapProps {
  memorial: MemorialResponse;
}

const INITIAL_BOUNDS: [[number, number], [number, number]] = [
  [34.2, 29.5],
  [35.9, 33.5],
];
const TILES_URL = "/tiles/israel.pmtiles";

function pulseWeight(mostRecentIncidentDate: string | null): number {
  if (!mostRecentIncidentDate) return 0;
  const incident = new Date(mostRecentIncidentDate).getTime();
  if (isNaN(incident)) return 0;
  const days = (Date.now() - incident) / (1000 * 60 * 60 * 24);
  return Math.max(0, 1 - days / 30);
}

function slugify(city: string): string {
  return city.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function MemorialMap({ memorial }: MemorialMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<Map | null>(null);
  const [selectedLocality, setSelectedLocality] = useState<Locality | null>(null);
  const [selectedCaseIndex, setSelectedCaseIndex] = useState<number | null>(null);
  const [screenPos, setScreenPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const yearMin = memorial.year_range.from ?? new Date().getFullYear();
  const yearMax = memorial.year_range.to ?? new Date().getFullYear();
  const [yearFrom, setYearFrom] = useState(yearMin);
  const [yearTo, setYearTo] = useState(yearMax);

  const filteredLocalities = useMemo(() => {
    return memorial.localities
      .map((loc) => {
        const deaths = loc.deaths.filter((d) => {
          if (!d.incident_date) return true;
          const y = Number(d.incident_date.slice(0, 4));
          return y >= yearFrom && y <= yearTo;
        });
        return { ...loc, deaths, death_count: deaths.length };
      })
      .filter((l) => l.death_count > 0);
  }, [memorial, yearFrom, yearTo]);

  const filteredLocalitiesRef = useRef(filteredLocalities);
  useEffect(() => {
    filteredLocalitiesRef.current = filteredLocalities;
  }, [filteredLocalities]);

  // Read URL on first paint
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const slug = params.get("locality");
    if (!slug) return;
    const loc = memorial.localities.find((l) => slugify(l.city) === slug);
    if (!loc) return;
    setSelectedLocality(loc);
    setScreenPos({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
    const caseStr = params.get("case");
    if (caseStr) {
      const idx = Number(caseStr);
      if (!isNaN(idx)) setSelectedCaseIndex(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Write URL on selection change
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (selectedLocality) {
      params.set("locality", slugify(selectedLocality.city));
      if (selectedCaseIndex !== null) {
        params.set("case", String(selectedCaseIndex));
      } else {
        params.delete("case");
      }
    } else {
      params.delete("locality");
      params.delete("case");
    }
    const qs = params.toString();
    const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }, [selectedLocality, selectedCaseIndex]);

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
      const features = filteredLocalities.map((loc) => ({
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
      });

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

      let raf = 0;
      const tick = () => {
        const t = performance.now() / 1000;
        const sine = (Math.sin(t * 1.8) + 1) / 2;
        map.setPaintProperty("locality-pulse", "circle-stroke-opacity", [
          "*",
          sine * 0.45,
          ["get", "pulse_weight"],
        ]);
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      map.once("remove", () => cancelAnimationFrame(raf));

      map.on("mouseenter", "locality-dot", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "locality-dot", () => {
        map.getCanvas().style.cursor = "";
      });

      map.on("click", "locality-dot", (e: MapLayerMouseEvent) => {
        const f = e.features?.[0];
        if (!f) return;
        const cityName = f.properties?.city as string | undefined;
        if (!cityName) return;
        const locality = filteredLocalitiesRef.current.find((l) => l.city === cityName);
        if (!locality) return;
        const pt = e.point;
        setScreenPos({ x: pt.x, y: pt.y });
        setSelectedLocality(locality);
        setSelectedCaseIndex(null);
      });
    });

    return () => {
      maplibregl.removeProtocol("pmtiles");
      map.remove();
      mapRef.current = null;
    };
  }, [memorial]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("localities");
    if (!src || src.type !== "geojson") return;
    const features = filteredLocalities.map((loc) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [loc.lng, loc.lat] },
      properties: {
        city: loc.city,
        death_count: loc.death_count,
        pulse_weight: pulseWeight(loc.most_recent_incident_date),
      },
    }));
    (src as maplibregl.GeoJSONSource).setData({ type: "FeatureCollection", features });
  }, [filteredLocalities]);

  const visibleCount = filteredLocalities.reduce((sum, l) => sum + l.death_count, 0);

  return (
    <div className="relative w-full h-screen" onClick={() => setSelectedLocality(null)}>
      <div ref={containerRef} className="absolute inset-0" />

      <div className="absolute top-3 left-4 z-20 text-xs font-medium text-neutral-700 tracking-wide">
        Crime Pipeline — Memorial
      </div>

      {memorial.run_id === null && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 text-xs text-neutral-500 bg-white/80 backdrop-blur px-2 py-1 rounded">
          Unable to load memorial data.
        </div>
      )}

      <DeathCount count={visibleCount} />

      <YearScrubber
        min={yearMin}
        max={yearMax}
        from={yearFrom}
        to={yearTo}
        onChange={(f, t) => {
          setYearFrom(f);
          setYearTo(t);
        }}
      />

      {selectedLocality && (
        <BloomCard
          locality={selectedLocality}
          initialCaseIndex={selectedCaseIndex}
          screenPos={screenPos}
          onClose={() => setSelectedLocality(null)}
          onSelectCase={setSelectedCaseIndex}
        />
      )}
    </div>
  );
}
