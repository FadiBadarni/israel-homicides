"""One-shot: populate lat/lng on data/gazetteer.json from a known coordinate map.

Coordinates are WGS84 decimal degrees, sourced from Wikidata/Wikipedia and
rounded to four decimal places (~11 m precision — more than enough for a
locality marker).
"""
from __future__ import annotations

import json
from pathlib import Path

# Known coordinates for every city currently in data/gazetteer.json.
# Keyed by name_en for stability across renames in non-English fields.
COORDS: dict[str, tuple[float, float]] = {
    "Arraba": (32.8517, 35.3361),
    "Sakhnin": (32.8633, 35.2961),
    "Deir Hanna": (32.8617, 35.3650),
    "Majd al-Krum": (32.9181, 35.2606),
    "Tamra": (32.8533, 35.1981),
    "Shfaram": (32.8056, 35.1700),
    "Kafr Kanna": (32.7444, 35.3417),
    "Nazareth": (32.7019, 35.2978),
    "Kafr Manda": (32.8133, 35.2547),
    "Iksal": (32.6856, 35.3133),
    "Yafa an-Naseriyye": (32.6911, 35.2961),
    "Reineh": (32.7261, 35.3056),
    "Kafr Yasif": (32.9544, 35.1639),
    "Acre": (32.9281, 35.0817),
    "Maghar": (32.8889, 35.4083),
    "Bi'ina": (32.9211, 35.2972),
    "Nahf": (32.9367, 35.3050),
    "Rameh": (32.9381, 35.3678),
    "Kabul": (32.8703, 35.2086),
    "I'billin": (32.8347, 35.1953),
    "Tur'an": (32.7853, 35.3739),
    "Umm al-Fahm": (32.5167, 35.1500),
    "Baqa al-Gharbiyye": (32.4150, 35.0367),
    "Jatt": (32.3950, 35.0517),
    "Ar'ara": (32.4942, 35.1019),
    "Kafr Qara": (32.5067, 35.0833),
    "Haifa": (32.7940, 34.9896),
    "Tira": (32.2333, 34.9500),
    "Taybe": (32.2683, 34.9569),
    "Qalansawe": (32.2858, 34.9839),
    "Lod": (31.9514, 34.8950),
    "Ramla": (31.9292, 34.8669),
    "Jaljulia": (32.1517, 34.9486),
    "Kafr Qasim": (32.1147, 34.9756),
    "Tel Aviv": (32.0853, 34.7818),
    "Jaffa": (32.0508, 34.7503),
    "Jerusalem": (31.7683, 35.2137),
    "Beit Safafa": (31.7458, 35.2086),
    "Abu Ghosh": (31.8064, 35.1075),
    "Rahat": (31.3950, 34.7561),
    "Hura": (31.3083, 34.9389),
    "Tel Sheva": (31.2300, 34.8939),
    "Lakiya": (31.3389, 34.9100),
    "Ksaifa": (31.2767, 34.9947),
    "Beersheba": (31.2517, 34.7917),
    "Kawkab Abu al-Hija": (32.8489, 35.2469),
    "Pardes Hanna-Karkur": (32.4731, 34.9747),
    "Yarka": (32.9658, 35.2125),
    "Arab al-Aramshe": (33.0644, 35.2911),
    "Yanuh-Jat": (32.9989, 35.2186),
    "Fureidis": (32.6133, 34.9389),
    "Shaqib al-Salam": (31.3211, 34.8769),
}


def main() -> None:
    path = Path("data/gazetteer.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing: list[str] = []
    for entry in data:
        name = entry["name_en"]
        coords = COORDS.get(name)
        if coords is None:
            missing.append(name)
            continue
        entry["lat"], entry["lng"] = coords
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if missing:
        print(f"WARNING: no coords for: {missing}")
    print(f"Updated {len(data) - len(missing)} entries.")


if __name__ == "__main__":
    main()
