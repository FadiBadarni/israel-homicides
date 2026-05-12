import type { StyleSpecification } from "maplibre-gl";

const LAND = "#f5f1ea";
const WATER = "#ece6db";
const COASTLINE = "#2c2a26";

/**
 * MapLibre style for the memorial map.
 *
 * Uses a self-hosted Protomaps .pmtiles file via the `pmtiles://` protocol
 * (registered by the runtime in `memorial-map.tsx` before the map mounts).
 *
 * Goals:
 *  - Cream land, slightly cooler cream water, charcoal coastline.
 *  - No road network, no place labels until deep zoom.
 *  - Nothing competes with the locality dots.
 */
export function buildMemorialStyle(tilesUrl: string): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
    sources: {
      protomaps: {
        type: "vector",
        url: `pmtiles://${tilesUrl}`,
        attribution: '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org">OSM</a>',
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": LAND } },
      {
        id: "water",
        type: "fill",
        source: "protomaps",
        "source-layer": "water",
        paint: { "fill-color": WATER },
      },
      {
        id: "coastline",
        type: "line",
        source: "protomaps",
        "source-layer": "natural",
        filter: ["==", ["get", "kind"], "coastline"],
        paint: { "line-color": COASTLINE, "line-width": 1 },
      },
      {
        id: "country-borders",
        type: "line",
        source: "protomaps",
        "source-layer": "boundaries",
        filter: ["==", ["get", "kind"], "country"],
        paint: { "line-color": COASTLINE, "line-width": 0.5, "line-dasharray": [3, 2] },
      },
      // Only show city labels at deep zoom (>=10)
      {
        id: "place-labels",
        type: "symbol",
        source: "protomaps",
        "source-layer": "places",
        minzoom: 10,
        layout: {
          "text-field": ["get", "name"],
          "text-size": 10,
          "text-font": ["Noto Sans Regular"],
        },
        paint: {
          "text-color": COASTLINE,
          "text-halo-color": LAND,
          "text-halo-width": 1,
        },
      },
    ],
  };
}
