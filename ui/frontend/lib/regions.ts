/**
 * Cultural-regional grouping of Israeli Arab localities.
 *
 * The four regions correspond to the conventional groupings used in
 * Israeli Arab civic discourse — they do NOT match Israel's official
 * administrative districts.
 */

export type RegionKey = "galilee" | "triangle" | "negev" | "mixed";

export const REGION_LABELS_AR: Record<RegionKey, string> = {
  galilee: "الجليل",
  triangle: "المثلّث",
  negev: "النقب",
  mixed: "المدن المختلطة",
};

const CITY_TO_REGION: Record<string, RegionKey> = {
  // Galilee
  "Arraba": "galilee",
  "Sakhnin": "galilee",
  "Deir Hanna": "galilee",
  "Majd al-Krum": "galilee",
  "Tamra": "galilee",
  "Shfaram": "galilee",
  "Kafr Kanna": "galilee",
  "Nazareth": "galilee",
  "Kafr Manda": "galilee",
  "Iksal": "galilee",
  "Yafa an-Naseriyye": "galilee",
  "Reineh": "galilee",
  "Kafr Yasif": "galilee",
  "Maghar": "galilee",
  "Bi'ina": "galilee",
  "Nahf": "galilee",
  "Rameh": "galilee",
  "Kabul": "galilee",
  "I'billin": "galilee",
  "Tur'an": "galilee",
  "Kawkab Abu al-Hija": "galilee",
  "Yarka": "galilee",
  "Arab al-Aramshe": "galilee",
  "Yanuh-Jat": "galilee",
  "Fureidis": "galilee",
  // Triangle
  "Umm al-Fahm": "triangle",
  "Baqa al-Gharbiyye": "triangle",
  "Jatt": "triangle",
  "Ar'ara": "triangle",
  "Kafr Qara": "triangle",
  "Tira": "triangle",
  "Taybe": "triangle",
  "Qalansawe": "triangle",
  "Jaljulia": "triangle",
  "Kafr Qasim": "triangle",
  // Negev
  "Rahat": "negev",
  "Hura": "negev",
  "Tel Sheva": "negev",
  "Lakiya": "negev",
  "Ksaifa": "negev",
  "Beersheba": "negev",
  "Shaqib al-Salam": "negev",
  // Mixed
  "Lod": "mixed",
  "Ramla": "mixed",
  "Tel Aviv": "mixed",
  "Jaffa": "mixed",
  "Jerusalem": "mixed",
  "Beit Safafa": "mixed",
  "Abu Ghosh": "mixed",
  "Haifa": "mixed",
  "Pardes Hanna-Karkur": "mixed",
  "Acre": "mixed",
};

export function regionFor(cityEn: string): RegionKey | null {
  return CITY_TO_REGION[cityEn] ?? null;
}
