import type { Lang } from "./language-context";

export const MISSING = "—";

export type TranslationKey =
  | "brand"
  | "nav.cases"
  | "nav.regions"
  | "nav.years"
  | "nav.about"
  | "hero.eyebrow"
  | "hero.h1"
  | "hero.lede"
  | "stats.last_year"
  | "stats.current_year_prefix"
  | "stats.current_year_suffix"
  | "stats.total"
  | "stats.under_40_pct"
  | "sec.cases_title"
  | "sec.cases_meta"
  | "filter.all"
  | "sec.regions_title"
  | "sec.regions_meta"
  | "sec.years_title"
  | "sec.years_meta_prefix"
  | "sec.years_meta_suffix"
  | "sec.about_title"
  | "about.col1.h"
  | "about.col1.p"
  | "about.col2.h"
  | "about.col2.p"
  | "about.col3.h"
  | "about.col3.p"
  | "footer.line1"
  | "footer.line2"
  | "no_cases"
  | "no_yearly"
  | "victim_word"
  | "current_label"
  | "tap_caption"
  | "case.in_memory"
  | "case.years_old"
  | "case.from"
  | "case.killed_on"
  | "case.facts_label"
  | "case.facts.date"
  | "case.facts.location"
  | "case.facts.cause"
  | "case.facts.suspect_status"
  | "case.sources_label"
  | "case.footer1"
  | "case.footer2"
  | "case.breadcrumb"
  | "case.load_failed"
  | "badge.documenting";

const T: Record<Lang, Record<TranslationKey, string>> = {
  ar: {
    "brand": "سجل الضحايا",
    "nav.cases": "القضايا",
    "nav.regions": "المناطق",
    "nav.years": "السنوات",
    "nav.about": "عن المشروع",
    "hero.eyebrow": "سجل عام · يُحدَّث أسبوعياً",
    "hero.h1": "كلّ ضحيّة لها اسم.\nوكل قضيّة لها قصّة.",
    "hero.lede":
      "سجلٌّ عامّ يُوثّق ضحايا جرائم القتل في المجتمع العربي في إسرائيل، اسماً تلو الآخر، استناداً إلى مصادر إخباريّة بالعربيّة والعبريّة.",
    "stats.last_year": "ضحيّة في عام {year} — حسب السجلّ.",
    "stats.current_year_prefix": "ضحيّة منذ بداية",
    "stats.current_year_suffix": "حتى الآن.",
    "stats.total": "قضيّة موثّقة في السجلّ منذ بدء التوثيق.",
    "stats.under_40_pct": "من الضحايا أعمارهم دون الأربعين عاماً.",
    "sec.cases_title": "القضايا الأحدث",
    "sec.cases_meta": "عرض كل القضايا",
    "filter.all": "الكلّ",
    "sec.regions_title": "حسب المنطقة",
    "sec.regions_meta": "منذ بداية التوثيق",
    "sec.years_title": "وراء كلّ رقم، إنسان",
    "sec.years_meta_prefix": "كلّ علامة",
    "sec.years_meta_suffix": "تُمثّل ضحيّة واحدة",
    "sec.about_title": "عن المشروع",
    "about.col1.h": "اسم لكلّ ضحيّة",
    "about.col1.p":
      "لا يُختزل أحدٌ إلى رقمٍ في إحصاء. كلّ قضيّة في هذا السجل تحمل اسماً ومدينةً وتاريخاً، وما أمكن جمعه من تفاصيل من مصادرها الأصليّة.",
    "about.col2.h": "من مصادر متعدّدة",
    "about.col2.p":
      "نجمع المعلومات من مواقع إخباريّة بالعربيّة والعبريّة (عرب 48، واي نت، والّا، وغيرها)، ونحفظ الأسماء كما وردت بلغاتها الأصليّة دون تحويلها.",
    "about.col3.h": "شفافيّة في الشك",
    "about.col3.p":
      "عندما تتعارض المصادر أو تكون المعلومات ناقصة، نُشير إلى ذلك بوضوح. الصدق في ما لا نعرفه جزء من احترامنا للضحايا وعائلاتهم.",
    "footer.line1": "سجلٌّ عام مستقلّ · لا يُمثّل أيّ جهةٍ رسميّة.",
    "footer.line2": "",
    "no_cases": "لا توجد قضايا تطابق هذه التصفية.",
    "no_yearly": "لا توجد بيانات سنويّة.",
    "victim_word": "ضحيّة",
    "current_label": "حتى الآن",
    "tap_caption":
      "كلّ علامةٍ هنا كانت إنساناً — أُماً أو أباً، ابناً أو ابنةً، صديقاً أو جاراً. هذا السجلّ موجود لئلّا يُنسى أحد منهم.",
    "case.in_memory": "في ذكرى",
    "case.years_old": "عاماً",
    "case.from": "من",
    "case.killed_on": "قُتل في",
    "case.facts_label": "تفاصيل الحادثة",
    "case.facts.date": "التاريخ",
    "case.facts.location": "المكان",
    "case.facts.cause": "السبب",
    "case.facts.suspect_status": "حالة المشتبه به",
    "case.sources_label": "المصادر",
    "case.footer1":
      "هذه الصفحة جزء من سجلٍّ عام يوثّق ضحايا الجريمة في المجتمع العربي في إسرائيل.",
    "case.footer2": "الأسماء والتفاصيل مُحفوظة كما وردت في مصادرها الأصليّة.",
    "case.breadcrumb": "سجل ضحايا الجريمة في المجتمع العربي في إسرائيل",
    "case.load_failed": "تعذّر تحميل القضيّة.",
    "badge.documenting": "قيد التوثيق",
  },
  he: {
    "brand": "רישום הקרבנות",
    "nav.cases": "תיקים",
    "nav.regions": "אזורים",
    "nav.years": "שנים",
    "nav.about": "על הפרויקט",
    "hero.eyebrow": "רישום ציבורי · מתעדכן שבועית",
    "hero.h1": "לכל קרבן יש שם.\nלכל מקרה יש סיפור.",
    "hero.lede":
      "רישום ציבורי המתעד את קרבנות מקרי הרצח בחברה הערבית בישראל, שם אחר שם, על בסיס מקורות חדשותיים בערבית ובעברית.",
    "stats.last_year": "קרבנות בשנת {year} — לפי הרישום.",
    "stats.current_year_prefix": "קרבנות מתחילת",
    "stats.current_year_suffix": "עד כה.",
    "stats.total": "תיקים מתועדים ברישום מאז תחילת התיעוד.",
    "stats.under_40_pct": "מהקרבנות בני פחות מארבעים.",
    "sec.cases_title": "תיקים אחרונים",
    "sec.cases_meta": "הצג את כל התיקים",
    "filter.all": "הכל",
    "sec.regions_title": "לפי אזור",
    "sec.regions_meta": "מאז תחילת התיעוד",
    "sec.years_title": "מאחורי כל מספר — אדם",
    "sec.years_meta_prefix": "כל סימן",
    "sec.years_meta_suffix": "מייצג קרבן אחד",
    "sec.about_title": "על הפרויקט",
    "about.col1.h": "שם לכל קרבן",
    "about.col1.p":
      "איש אינו מצטמצם למספר בסטטיסטיקה. כל תיק ברישום זה נושא שם, עיר ותאריך, וכל פרט שניתן היה לאסוף ממקורותיו המקוריים.",
    "about.col2.h": "ממקורות מרובים",
    "about.col2.p":
      "אנו אוספים מידע מאתרי חדשות בערבית ובעברית (ערב 48, וואינט, וואלה ועוד), ושומרים את השמות כפי שהופיעו בשפתם המקורית.",
    "about.col3.h": "שקיפות בספק",
    "about.col3.p":
      "כאשר המקורות סותרים זה את זה או שהמידע חסר, אנו מציינים זאת בבירור. הכנות לגבי מה שאיננו יודעים היא חלק מהכבוד שלנו לקרבנות ולמשפחותיהם.",
    "footer.line1": "רישום ציבורי עצמאי · אינו מייצג גוף רשמי.",
    "footer.line2": "",
    "no_cases": "אין תיקים התואמים את הסינון.",
    "no_yearly": "אין נתונים שנתיים.",
    "victim_word": "קרבן",
    "current_label": "עד כה",
    "tap_caption":
      "כל סימן כאן היה אדם — אם או אב, בן או בת, חבר או שכן. הרישום הזה קיים כדי שאיש מהם לא יישכח.",
    "case.in_memory": "לזכר",
    "case.years_old": "בני",
    "case.from": "מ-",
    "case.killed_on": "נרצח ב-",
    "case.facts_label": "פרטי האירוע",
    "case.facts.date": "תאריך",
    "case.facts.location": "מקום",
    "case.facts.cause": "סיבה",
    "case.facts.suspect_status": "סטטוס החשוד",
    "case.sources_label": "מקורות",
    "case.footer1":
      "עמוד זה הוא חלק מרישום ציבורי המתעד את קרבנות הפשע בחברה הערבית בישראל.",
    "case.footer2": "השמות והפרטים נשמרים כפי שהופיעו במקורותיהם המקוריים.",
    "case.breadcrumb": "רישום קרבנות הפשע בחברה הערבית בישראל",
    "case.load_failed": "טעינת התיק נכשלה.",
    "badge.documenting": "בתהליך תיעוד",
  },
};

export function t(lang: Lang, key: TranslationKey, vars?: Record<string, string | number>): string {
  let s = T[lang][key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.replaceAll(`{${k}}`, String(v));
    }
  }
  return s;
}

/**
 * Pick the language-matching field, or return MISSING when null/empty.
 * Strict: does NOT fall back to the other language.
 */
export function pickLangField(
  ar: string | null | undefined,
  he: string | null | undefined,
  lang: Lang,
): string {
  const v = lang === "ar" ? ar : he;
  return v && v.trim() ? v : MISSING;
}
