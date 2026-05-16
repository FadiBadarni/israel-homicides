"""Add ~50 missing Israeli/Arab-society localities to the gazetteer.

Sourced from the 133 unresolved cases in canonical_cases. Coordinates
from Wikipedia / OpenStreetMap. Each entry contains canonical Arabic /
Hebrew / English names + common aliases so memorial endpoint resolves
all spelling variants.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT)


# (name_en, name_ar, name_he, district, lat, lng, aliases_ar, aliases_he, aliases_en, region)
NEW_ENTRIES = [
    # High-count missing towns
    ("I'blin",            "عبلين",            "אעבלין",          "Northern", 32.8242, 35.2003, ["إبلين"],          ["איבלין", "עיבלין"], ["Iblin","Ibillin"], "Galilee"),
    ("Musmus",            "مصمص",             "מוסמוס",          "Haifa",    32.5283, 35.0908, ["مصمصة"],          ["מסמס"],         [],                  "Wadi Ara"),
    ("Majdal Shams",      "مجدل شمس",         "מג'דל שמס",       "Northern", 33.2683, 35.7800, [],                  ["מג'דל שאמס","מג'דל אל שמס"], ["Majdal al-Shams"], "Golan Heights"),
    ("Nof HaGalil",       "نوف هجليل",        "נוף הגליל",       "Northern", 32.7172, 35.3128, ["نوف هغليل","نوف الجليل"], ["נוף-הגליל","נצרת עילית"], ["Nof Hagalil","Nazareth Illit"], "Galilee"),
    ("Tuba-Zangariyye",   "طوبا الزنغرية",    "טובא-זנגרייה",    "Northern", 32.9883, 35.5564, ["طوبا الزنغريّة","الزنغرية"], ["טובא זנגריה","טובה זנגריה"], ["Tuba","Tuba Zangariyye"], "Galilee"),
    ("Arab al-Khawalid",  "عرب الخوالد",      "ערב אל-ח'ואלד",   "Northern", 32.7456, 35.0817, ["الخوالد","عرب الخوالدة"], ["ח'ואלד","אל ח'ואלד"], ["Khawalid","Khawaled"], "Galilee"),
    ("Jadeidi-Makr",      "جديدة المكر",      "ג'דיידה-מכר",     "Northern", 32.9050, 35.1583, ["جديدة-المكر","الجديدة المكر","الجديدة-المكر","الجديدة - المكر","جديدة - المكر"], ["ג'דיידה מכר","מכר","ג'דיידה"], ["Jadeidi","Makr"], "Galilee"),
    ("Nahariya",          "نهريا",            "נהריה",           "Northern", 33.0079, 35.0950, ["نهاريا"],          [],               [],                  "Western Galilee"),
    ("Daburiyya",         "دبورية",           "דבוריה",          "Northern", 32.7028, 35.3839, ["دبّورية"],         ["דבוריא"],       ["Dabburiya"],       "Galilee"),
    ("al-Mazra'a",        "المزرعة",          "מזרעה",           "Northern", 33.0028, 35.1419, ["مزرعة"],           ["מזרעא"],        ["Mazra'a","Mazraa"], "Western Galilee"),
    ("Wadi al-Na'am",     "وادي النعم",       "ואדי אל-נעם",     "Southern", 31.2342, 34.8503, ["وادي النعام"],     ["ואדי נעם"],     ["Wadi an-Na'am","Wadi al-Naam"], "Negev"),
    ("Dahamash",          "دهمش",             "דהמש",            "Central",  31.9853, 34.8728, ["دهامش","دحمش"],   ["דהאמש"],        ["Dahmash","Daheishe"], "Lod area"),
    ("Maghar",            "مغار",             "מע'אר",           "Northern", 32.8917, 35.4083, ["المغار","مرار"],   ["מרר","מע'אר","מגאר"], ["Mghar","Maghar","Magar"], "Galilee"),
    ("Tirat Carmel",      "طيرة الكرمل",      "טירת כרמל",       "Haifa",    32.7639, 35.0064, ["طيرة كرمل"],       ["טירת הכרמל"],   ["Tirat HaKarmel"],  "Haifa"),
    ("Beit Jann",         "بيت جن",           "בית ג'ן",         "Northern", 32.9669, 35.3792, ["بيت جان"],         ["בית ג'אן"],     ["Beit Jan"],        "Galilee"),
    ("Hurfeish",          "حرفيش",            "חורפיש",          "Northern", 33.0386, 35.3464, ["حرفيش"],           [],               ["Khurfeish"],       "Galilee"),
    ("Isfiya",            "عسفيا",            "עוספיה",          "Haifa",    32.7400, 35.0667, ["عسفيه","عوسفيا"], ["איספיא","עיספיא"], ["Usfiyya","Isfiyya"], "Mount Carmel"),
    ("Ein Mahil",         "عين ماهل",         "עין מאהל",        "Northern", 32.7253, 35.3614, [],                  ["עין-מאהל"],     ["Ein Mahel"],       "Galilee"),
    ("Yokneam",           "يوكنعام",          "יקנעם",           "Haifa",    32.6594, 35.1067, ["يوقنعام"],         ["יוקנעם"],       ["Yokneam Illit"],   "Jezreel Valley"),
    ("Nahalal",           "نهلال",            "נהלל",            "Northern", 32.6961, 35.1981, [],                  [],               [],                  "Jezreel Valley"),
    ("Eilabun",           "عيلبون",           "עילבון",          "Northern", 32.8261, 35.3936, ["إيلبون"],          ["איילבון"],      ["Aylabun","Ailabun"], "Galilee"),
    ("Sajur",             "ساجور",            "סאג'ור",          "Northern", 32.9272, 35.3678, [],                  ["סאג׳ור"],       ["Sajour"],          "Galilee"),
    ("Tarshiha",          "ترشيحا",           "תרשיחא",          "Northern", 33.0250, 35.2853, [],                  ["מעלות-תרשיחא"], ["Tarshiha","Ma'alot-Tarshiha"], "Western Galilee"),
    ("Kafr Bara",         "كفر برا",          "כפר ברא",         "Central",  32.1308, 34.9528, [],                  ["כפר ברה"],      [],                  "Triangle"),
    ("al-Mash'had",       "المشهد",           "אל-משהד",         "Northern", 32.7414, 35.3303, ["مشهد"],            ["משהד","משחד"],  ["Mashhad","Meshhed"], "Galilee"),
    ("Wadi al-Hamam",     "وادي الحمام",      "ואדי חמאם",       "Northern", 32.8367, 35.4953, [],                  ["ואדי אל-חמאם"], [],                  "Galilee"),
    ("Shtula",            "شتولا",            "שתולה",           "Northern", 33.0617, 35.2811, [],                  [],               [],                  "Western Galilee"),
    ("Karmiel",           "كرميئيل",          "כרמיאל",          "Northern", 32.9192, 35.3038, ["كرمئيل"],          [],               [],                  "Galilee"),
    ("Nesher",            "نيشر",             "נשר",             "Haifa",    32.7681, 35.0408, [],                  [],               [],                  "Haifa"),
    ("Mu'awiya",          "معاوية",           "מועאויה",         "Haifa",    32.5667, 35.1283, [],                  ["מעוויה"],       ["Muawiya"],         "Wadi Ara"),
    ("Kisra-Sumei",       "كسرى سميع",        "כסרא-סמיע",       "Northern", 32.9794, 35.3431, ["كسرى-سميع"],       ["כיסרא-סומיע"],  ["Kisra Sumei"],     "Galilee"),
    ("Tamra al-Zu'biyya", "طمرة الزعبية",     "טמרה הזעבייה",    "Northern", 32.6442, 35.4072, [],                  ["טמרה זעביה"],   [],                  "Lower Galilee"),
    ("Sulam",             "سولم",             "סולם",            "Northern", 32.6058, 35.3358, [],                  [],               [],                  "Jezreel Valley"),
    ("Rosh Pina",         "روش بينا",         "ראש פינה",        "Northern", 32.9686, 35.5419, [],                  ["ראש פנה"],      ["Rosh Pinna"],      "Galilee"),
    ("Herzliya",          "هرتسليا",          "הרצליה",          "Tel Aviv", 32.1633, 34.8442, ["هرتزليا","هرتسليه"], [],            [],                  "Coastal"),
    ("Ka'abiyya",         "الكعبية",          "כעבייה",          "Northern", 32.7392, 35.1808, ["كعبية"],           ["כעבייה-טבאש-חג'אג'רה"], ["Ka'abiyya-Tabbash-Hajajre"], "Galilee"),
    ("Bir Hadaj",         "بئر هداج",         "ביר הדאג'",       "Southern", 30.9181, 34.7639, ["بئر هدّاج","بير هداج"], ["ביר הדאג"], ["Bir al-Hadaj"], "Negev"),
    ("Umm Batin",         "أم بطين",          "אום בטין",        "Southern", 31.2706, 34.8919, ["أم بطين (أبو كف)"], ["אום בטין"], ["Umm Batin"],     "Negev"),
    ("Abu Qrenat",        "أبو قرينات",       "אבו קוידר",       "Southern", 31.1864, 34.9889, [],                  [],               ["Abu Krinat"],      "Negev"),
    ("Abu Tlul",          "أبو تلول",         "אבו תלול",        "Southern", 31.1842, 34.8331, [],                  [],               ["Abu Talul"],       "Negev"),
    ("Drijat",            "الدريجات",         "דריג'את",         "Southern", 31.2792, 34.9319, ["دريجات"],          [],               [],                  "Negev"),
    ("Tel Arad",          "تل عراد",          "תל ערד",          "Southern", 31.2861, 35.1492, [],                  [],               [],                  "Negev"),
    ("Arad",              "عراد",             "ערד",             "Southern", 31.2589, 35.2125, [],                  [],               [],                  "Negev"),
    ("Eilat",             "إيلات",            "אילת",            "Southern", 29.5577, 34.9519, ["ايلات"],           [],               [],                  "Negev"),
    ("Re'im",             "رعيم",             "רעים",            "Southern", 31.3225, 34.4664, [],                  [],               [],                  "Western Negev"),
    ("Tel Mond",          "تل موند",          "תל מונד",         "Central",  32.2528, 34.9128, [],                  [],               [],                  "Sharon"),
    ("Tiberias",          "طبرية",            "טבריה",           "Northern", 32.7959, 35.5311, [],                  [],               [],                  "Galilee"),
    ("Kiryat Haim",       "كريات حاييم",      "קריית חיים",      "Haifa",    32.8242, 35.0489, [],                  ["קרית חיים"],    [],                  "Haifa"),
    ("Kiryat Yam",        "كريات يام",        "קריית ים",        "Haifa",    32.8456, 35.0681, [],                  ["קרית ים"],      [],                  "Haifa"),
    ("Pisgat Ze'ev",      "بسغات زئيف",       "פסגת זאב",        "Jerusalem",31.8253, 35.2533, ["بسغات זאב"],       [],               [],                  "Jerusalem"),
    ("Shu'fat Camp",      "مخيم شعفاط",       "מחנה שועפט",      "Jerusalem",31.8147, 35.2375, ["شعفاط"],           ["שועפאט"],       ["Shuafat","Shu'afat"], "Jerusalem"),
    ("East Jerusalem",    "القدس الشرقية",    "מזרח ירושלים",    "Jerusalem",31.7833, 35.2367, [],                  [],               [],                  "Jerusalem"),
    ("Neot Hovav",        "نيؤوت حوفاف",      "נאות חובב",       "Southern", 31.1417, 34.8417, [],                  [],               [],                  "Negev"),
    ("Fureidis",          "فريديس",           "פוריידיס",        "Haifa",    32.6531, 34.9636, [],                  ["פורדייס"],      ["Furaydis"],        "Carmel coast"),
    ("Tel Dan",           "تل دان",           "תל דן",           "Northern", 33.2467, 35.6536, [],                  [],               [],                  "Galilee Panhandle"),
    ("Nitzanei Oz",       "ניצני עוז",        "ניצני עוז",        "Central",  32.3225, 34.9747, [],                  [],               [],                  "Sharon"),
    ("Qalqilya",          "قلقيلية",          "קלקיליה",         "West Bank",32.1903, 34.9719, [],                  [],               [],                  "West Bank"),
    ("Ness Ziona",        "نس تسيونا",        "נס ציונה",        "Central",  31.9300, 34.8000, [],                  [],               ["Nes Ziona"],       "Coastal"),
    ("Modi'in",           "موديعين",          "מודיעין",         "Central",  31.8989, 35.0089, ["مودعين"],          ["מודיעין-מכבים-רעות"], ["Modiin"],   "Central"),
    ("Wadi Salama",       "وادي سلامة",       "ואדי סלמה",       "Northern", 32.7900, 35.2700, ["وادي سلّامة"],     [],               [],                  "Galilee"),
    ("Wadi Ara",          "وادي عارة",        "ואדי עארה",       "Haifa",    32.5018, 35.0986, [],                  ["ואדי ערה"],     [],                  "Wadi Ara"),
]


def main() -> None:
    path = Path("data/gazetteer.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    before = len(data)

    # Map existing names so we don't duplicate
    existing_keys = set()
    for e in data:
        existing_keys.add((e.get("name_en") or "").lower())
        existing_keys.add(e.get("name_ar") or "")

    added = 0
    skipped: list[str] = []
    for entry in NEW_ENTRIES:
        en, ar, he, district, lat, lng, a_ar, a_he, a_en, region = entry
        if en.lower() in existing_keys or ar in existing_keys:
            skipped.append(f"{en} ({ar}) — already in gazetteer")
            continue
        rec = {
            "name_en": en, "name_ar": ar, "name_he": he,
            "district": district, "lat": lat, "lng": lng,
        }
        if region: rec["region"] = region
        if a_ar:   rec["aliases_ar"] = a_ar
        if a_he:   rec["aliases_he"] = a_he
        if a_en:   rec["aliases_en"] = a_en
        data.append(rec)
        added += 1

    # Add Hebrew alias אום אל פחם to existing Umm al-Fahm entry, and
    # other spelling fixes that map to existing entries.
    alias_fixes = [
        ("Umm al-Fahm",  ["אום אל פחם","אום אל-פחם","אום אל פאחם"], "aliases_he"),
        ("Kiryat Ata",   ["كريات آتا","كريات أتا"],                   "aliases_ar"),
        ("Zarzir",       ["الزرازير"],                                  "aliases_ar"),
        ("Maghar",       ["מרר"],                                       "aliases_he"),
    ]
    for target_en, new_aliases, field in alias_fixes:
        for e in data:
            if (e.get("name_en") or "").lower() == target_en.lower():
                cur = e.get(field) or []
                for a in new_aliases:
                    if a not in cur:
                        cur.append(a)
                e[field] = cur
                break

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Added {added} new entries (gazetteer {before} → {len(data)})")
    if skipped:
        print("Skipped (already present):")
        for s in skipped: print(f"  {s}")


if __name__ == "__main__":
    main()
