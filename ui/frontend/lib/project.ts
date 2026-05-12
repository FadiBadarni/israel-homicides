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
 * Rosh Hanikra (NW). ~25 points — coarse but recognisable.
 */
export const ISRAEL_OUTLINE_LATLNG: ReadonlyArray<[number, number]> = [
  // Mediterranean coast, north → south
  [33.08, 35.10],
  [32.93, 35.08],
  [32.79, 34.99],
  [32.50, 34.91],
  [32.08, 34.78],
  [31.80, 34.64],
  [31.52, 34.45],
  [31.29, 34.25],
  // Egyptian (Sinai) border, north → south
  [30.95, 34.43],
  [30.60, 34.65],
  [30.10, 34.75],
  [29.55, 34.95],
  // Gulf of Aqaba / Jordan border, south → north
  [29.60, 34.98],
  [30.50, 35.13],
  [30.95, 35.31],
  [31.30, 35.41],
  [31.78, 35.46],
  [32.39, 35.56],
  [32.78, 35.57],
  [33.10, 35.66],
  // Northern border (Syria + Lebanon), east → west
  [33.27, 35.62],
  [33.27, 35.45],
  [33.08, 35.10],
];
