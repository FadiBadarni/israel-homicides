/**
 * Equirectangular projection from WGS84 lat/lng to the memorial-map SVG viewport.
 *
 * For Israel's narrow extent (~4° lat × 1.7° lng) the distortion vs Mercator
 * is visually negligible. Linear math keeps the projection invertible and
 * tests deterministic.
 */

export const ISRAEL_BOUNDS = {
  minLat: 29.5,
  maxLat: 33.5,
  minLng: 34.2,
  maxLng: 35.9,
} as const;

export const MAP_WIDTH = 360;
export const MAP_HEIGHT = 860;

export function projectLatLng(lat: number, lng: number): { x: number; y: number } {
  const { minLat, maxLat, minLng, maxLng } = ISRAEL_BOUNDS;
  const x = ((lng - minLng) / (maxLng - minLng)) * MAP_WIDTH;
  const y = ((maxLat - lat) / (maxLat - minLat)) * MAP_HEIGHT;
  return { x, y };
}

/**
 * Approximate Israel + West Bank + Gaza boundary, traversed clockwise from
 * Rosh Hanikra (NW). ~40 points — coarse but recognisable.
 */
export const ISRAEL_OUTLINE_LATLNG: ReadonlyArray<[number, number]> = [
  // Mediterranean coast, north → south
  [33.083, 35.107], // Rosh Hanikra (Lebanon border on coast)
  [33.000, 35.103],
  [32.928, 35.082], // Acre
  [32.832, 35.045],
  [32.794, 34.989], // Haifa
  [32.625, 34.927], // Atlit
  [32.412, 34.879], // Hadera
  [32.317, 34.853],
  [32.166, 34.815], // Herzliya
  [32.085, 34.768], // Tel Aviv-Yafo
  [31.964, 34.733],
  [31.797, 34.638], // Ashdod
  [31.668, 34.572], // Ashkelon
  [31.521, 34.448], // Gaza City coast
  [31.430, 34.342],
  [31.358, 34.260], // Rafah / Egypt-Gaza-Israel triple
  // Egyptian (Sinai) border — NE inland, then SW
  [31.230, 34.395],
  [31.083, 34.480],
  [30.952, 34.435],
  [30.733, 34.580],
  [30.435, 34.668],
  [30.137, 34.756],
  [29.811, 34.838],
  [29.553, 34.957], // Eilat / Gulf of Aqaba
  // Jordan border, north to Dead Sea, on to Galilee
  [29.913, 35.072],
  [30.176, 35.155],
  [30.434, 35.205],
  [30.913, 35.367],
  [31.105, 35.434], // south Dead Sea
  [31.500, 35.484], // east of Dead Sea
  [31.762, 35.555],
  [32.092, 35.567],
  [32.388, 35.567],
  [32.687, 35.572],
  [32.985, 35.661], // north of Sea of Galilee
  // Golan / Northern border
  [33.115, 35.825], // Mount Hermon area
  [33.305, 35.792],
  [33.292, 35.625],
  [33.262, 35.450],
  [33.190, 35.250],
  [33.108, 35.142], // close near Rosh Hanikra
];

/**
 * Cluster localities by geographic proximity using union-find on a
 * distance threshold in degrees. Two localities are in the same cluster
 * if their Euclidean distance in lat/lng space is <= threshold.
 *
 * 0.18° ≈ 20 km — chosen to group adjacent Arab towns in the Galilee
 * and the Bedouin settlements in the Negev, without bridging entire
 * regions into one super-cluster.
 */
export function findClusters<T extends { lat: number; lng: number; city: string }>(
  items: T[],
  thresholdDegrees: number = 0.18,
): T[][] {
  if (items.length === 0) return [];
  const parent = items.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const union = (a: number, b: number) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  };

  for (let i = 0; i < items.length; i++) {
    for (let j = i + 1; j < items.length; j++) {
      const dlat = items[i].lat - items[j].lat;
      const dlng = items[i].lng - items[j].lng;
      if (dlat * dlat + dlng * dlng <= thresholdDegrees * thresholdDegrees) {
        union(i, j);
      }
    }
  }

  const groups = new Map<number, T[]>();
  for (let i = 0; i < items.length; i++) {
    const root = find(i);
    const arr = groups.get(root) ?? [];
    arr.push(items[i]);
    groups.set(root, arr);
  }
  return Array.from(groups.values());
}

/**
 * Given a cluster of localities, return the centroid + radius (in projected
 * SVG units) that contains all of them with a small padding.
 */
export function clusterGeometry(cluster: { lat: number; lng: number }[]): {
  cx: number;
  cy: number;
  r: number;
} {
  const pts = cluster.map((c) => projectLatLng(c.lat, c.lng));
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  const r = Math.max(...pts.map((p) => Math.hypot(p.x - cx, p.y - cy))) + 18;
  return { cx, cy, r };
}

export interface CityPolygons {
  polygons: Record<string, [number, number][]>;
}

/**
 * Project a ring of [lng, lat] WGS84 points into SVG-space polyline string.
 */
export function projectRing(ring: [number, number][]): string {
  return ring
    .map(([lng, lat]) => projectLatLng(lat, lng))
    .map(({ x, y }) => `${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
}
