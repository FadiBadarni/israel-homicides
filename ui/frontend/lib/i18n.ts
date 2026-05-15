import type { Lang } from "./language-context";

export const MISSING = "—";

export type TranslationKey =
  | "brand"
  | "nav.contact"
  | "nav.contribute"
  | "nav.back_to_register"
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
  | "search.placeholder"
  | "search.matches"
  | "search.clear"
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
  | "contact.lede"
  | "contact.cta"
  | "contribute.eyebrow"
  | "contribute.h1"
  | "contribute.lede"
  | "contribute.sec_ways_title"
  | "contribute.sec_ways_meta"
  | "contribute.card1.h"
  | "contribute.card1.p"
  | "contribute.card2.h"
  | "contribute.card2.p"
  | "contribute.card3.h"
  | "contribute.card3.p"
  | "contribute.card4.h"
  | "contribute.card4.p"
  | "contribute.card5.h"
  | "contribute.card5.p"
  | "contribute.card6.h"
  | "contribute.card6.p"
  | "contribute.sec_close_title"
  | "contribute.close_lede"
  | "contribute.close_cta"
  | "footer.line1"
  | "footer.line2"
  | "no_cases"
  | "no_yearly"
  | "victim_word"
  | "current_label"
  | "tap_caption"
  | "tap.progress_note"
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
  | "badge.documenting"
  | "pagination.prev"
  | "pagination.next"
  | "pagination.page_of";

const T: Record<Lang, Record<TranslationKey, string>> = {
  ar: {
    "brand": "سجل الضحايا",
    "nav.contact": "تواصل",
    "nav.contribute": "المُشاركة",
    "nav.back_to_register": "العودة إلى السجل",
    "hero.eyebrow": "سجل عام · يُحدَّث أسبوعياً",
    "hero.h1": "كلّ ضحيّة لها اسم.\nوكل قضيّة لها قصّة.",
    "hero.lede":
      "سجلٌّ عامّ يُوثّق ضحايا جرائم القتل في المجتمع العربي في إسرائيل، اسماً تلو الآخر، استناداً إلى مصادر إخباريّة بالعربيّة والعبريّة.",
    "stats.last_year": "ضحيّة في عام {year}.",
    "stats.current_year_prefix": "منذ بداية",
    "stats.current_year_suffix": "حتى الآن.",
    "stats.total": "اسماً في السجلّ.",
    "stats.under_40_pct": "دون الأربعين عاماً.",
    "sec.cases_title": "القضايا الأحدث",
    "sec.cases_meta": "عرض كل القضايا",
    "filter.all": "الكلّ",
    "search.placeholder": "ابحث باسم أو بمدينة…",
    "search.matches": "{n} نتيجة",
    "search.clear": "مسح البحث",
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
    "contact.lede":
      "هل لاحظت خطأً في معلومة أو نقصاً في السجلّ؟ تواصل معي عبر موقعي الشخصي.",
    "contact.cta": "تواصل معي",
    "contribute.eyebrow": "دعوةٌ لمشاركة العمل",
    "contribute.h1": "هذا العملُ ثقيل.\nلا نقدرُ عليه وحدنا.",
    "contribute.lede":
      "توثيقُ كلِّ ضحيّة — اسمَها الكامل، وظروفَ رحيلها، وصورتَها إن وُجدت، وذاكرةَ أهلها — يتطلّبُ عنايةً أكبر ممّا تتّسعُ له مجموعةٌ صغيرة. إن كان لديك ما تُضيفه إلى هذا السجلّ، أو ما تُساهم به في حملِ هذا العمل، فأنت موضعُ ترحيب.",
    "contribute.sec_ways_title": "سُبُلُ المُشاركة",
    "contribute.sec_ways_meta": "ستّ طرق لإغناء السجلّ",
    "contribute.card1.h": "تصحيحُ معلومة",
    "contribute.card1.p":
      "هل لاحظت خطأً في اسم، أو في تاريخ، أو في تفصيل من قضيّة؟ حتى أصغرُ تصحيح يُضيفُ إلى دقّة السجلّ.",
    "contribute.card2.h": "إضافةُ اسمٍ غائب",
    "contribute.card2.p":
      "إن كنتَ تعرفُ ضحيّةً لم تُذكر بعد، خاصّةً في مناطق لا تصلها التغطيةُ الإخباريّة بسهولة، أرسل لنا ما تعرفُ.",
    "contribute.card3.h": "ذكرى أو صورة",
    "contribute.card3.p":
      "جملةٌ تصفُ من كان الضحية، أو صورةٌ بإذنٍ من الأهل، تُحوّلُ الإحصاء إلى وجهٍ ومسيرة.",
    "contribute.card4.h": "مساعدةٌ في الترجمة",
    "contribute.card4.p":
      "بعضُ المصادر تظهرُ بلغةٍ واحدة فقط. مساعدتُكَ في الترجمة بين العربيّة والعبريّة والإنجليزيّة تُغني السجلّ، وتُتيحُ للأهل الوصولَ إليه.",
    "contribute.card5.h": "تنبيهٌ من مصدرٍ محلّي",
    "contribute.card5.p":
      "قضيّةٌ نُشرت في صحيفةٍ محلّيّة أو منشورٍ على وسائل التواصل ولا تصلُ إلى السجلّ؟ أرسل لنا الرابط، نُتابعُه.",
    "contribute.card6.h": "نشرُ السجلّ",
    "contribute.card6.p":
      "شارك السجلّ مع من قد يحتاجُه أو من يستطيعُ المساهمةَ فيه. كلُّ عائلةٍ تجدُه تصيرُ قادرةً على التأكّد من ذكر أحبّتها.",
    "contribute.sec_close_title": "للتواصل",
    "contribute.close_lede":
      "للمشاركةِ بأيٍّ من هذه السُّبُل، أو لطرحِ سؤال، يمكنُك الوصولُ إليّ عبر موقعي الشخصيّ.",
    "contribute.close_cta": "تواصل عبر موقعي",
    "footer.line1": "سجلٌّ عام مستقلّ · لا يُمثّل أيّ جهةٍ رسميّة.",
    "footer.line2": "",
    "no_cases": "لا توجد قضايا تطابق هذه التصفية.",
    "no_yearly": "لا توجد بيانات سنويّة.",
    "victim_word": "ضحيّة",
    "current_label": "حتى الآن",
    "tap_caption":
      "كلّ علامةٍ هنا كانت إنساناً — أُماً أو أباً، ابناً أو ابنةً، صديقاً أو جاراً. هذا السجلّ موجود لئلّا يُنسى أحد منهم.",
    "tap.progress_note":
      "البحثُ مستمرّ · تُضافُ ضحايا من السنوات السابقة كلّما توفّرت معلوماتٌ موثّقة عنهم.",
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
    "pagination.prev": "السابق",
    "pagination.next": "التالي",
    "pagination.page_of": "صفحة {page} من {total}",
  },
  he: {
    "brand": "רישום הקרבנות",
    "nav.contact": "צרו קשר",
    "nav.contribute": "השתתפות",
    "nav.back_to_register": "חזרה לרישום",
    "hero.eyebrow": "רישום ציבורי · מתעדכן שבועית",
    "hero.h1": "לכל קרבן יש שם.\nלכל מקרה יש סיפור.",
    "hero.lede":
      "רישום ציבורי המתעד את קרבנות מקרי הרצח בחברה הערבית בישראל, שם אחר שם, על בסיס מקורות חדשותיים בערבית ובעברית.",
    "stats.last_year": "קרבנות בשנת {year}.",
    "stats.current_year_prefix": "מתחילת",
    "stats.current_year_suffix": "עד כה.",
    "stats.total": "שמות ברישום.",
    "stats.under_40_pct": "מתחת לגיל 40.",
    "sec.cases_title": "תיקים אחרונים",
    "sec.cases_meta": "הצג את כל התיקים",
    "filter.all": "הכל",
    "search.placeholder": "חיפוש לפי שם או עיר…",
    "search.matches": "{n} תוצאות",
    "search.clear": "נקה חיפוש",
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
    "contact.lede":
      "זיהיתם טעות או מידע חסר ברישום? פנו אליי דרך האתר האישי שלי.",
    "contact.cta": "צרו קשר",
    "contribute.eyebrow": "הזמנה להשתתפות",
    "contribute.h1": "המלאכה הזו כבדה.\nאיננו יכולים לעמוד בה לבדנו.",
    "contribute.lede":
      "תיעוד של כל קרבן — השם המלא, נסיבות הפטירה, תצלום אם קיים, וזיכרון המשפחה — דורש יותר תשומת לב ממה שקבוצה קטנה יכולה להחזיק. אם יש בידכם מה להוסיף לרישום, או דרך לסייע במלאכה, אתם מוזמנים.",
    "contribute.sec_ways_title": "דרכים להשתתף",
    "contribute.sec_ways_meta": "שש דרכים להעשיר את הרישום",
    "contribute.card1.h": "תיקון פרט",
    "contribute.card1.p":
      "זיהיתם טעות בשם, בתאריך או בפרט בתיק? אפילו התיקון הקטן ביותר מוסיף לדיוק הרישום.",
    "contribute.card2.h": "הוספת שם חסר",
    "contribute.card2.p":
      "אם ידוע לכם על קרבן שטרם תועד, במיוחד באזורים שאינם נחשפים בקלות לתקשורת, אנא הביאו לידיעתנו.",
    "contribute.card3.h": "זיכרון או תצלום",
    "contribute.card3.p":
      "משפט המתאר את האדם, או תצלום באישור המשפחה, הופכים מספר לפנים ולסיפור.",
    "contribute.card4.h": "עזרה בתרגום",
    "contribute.card4.p":
      "חלק מהמקורות זמינים רק בשפה אחת. עזרתכם בתרגום בין ערבית, עברית ואנגלית מעשירה את הרישום ומאפשרת למשפחות להגיע אליו.",
    "contribute.card5.h": "דיווח ממקור מקומי",
    "contribute.card5.p":
      "סיפור שפורסם בעיתון מקומי או בפוסט ברשתות החברתיות ולא הגיע לרישום? שלחו את הקישור — נטפל בו.",
    "contribute.card6.h": "הפצת הרישום",
    "contribute.card6.p":
      "שתפו את הרישום עם מי שעשוי לזקוק לו או לסייע בו. כל משפחה שמגיעה אליו יכולה לוודא שיקיריה זכורים.",
    "contribute.sec_close_title": "ליצירת קשר",
    "contribute.close_lede":
      "להשתתפות בכל אחת מהדרכים שלמעלה, או לכל שאלה, ניתן להגיע אליי דרך האתר האישי שלי.",
    "contribute.close_cta": "לאתר האישי",
    "footer.line1": "רישום ציבורי עצמאי · אינו מייצג גוף רשמי.",
    "footer.line2": "",
    "no_cases": "אין תיקים התואמים את הסינון.",
    "no_yearly": "אין נתונים שנתיים.",
    "victim_word": "קרבן",
    "current_label": "עד כה",
    "tap_caption":
      "כל סימן כאן היה אדם — אם או אב, בן או בת, חבר או שכן. הרישום הזה קיים כדי שאיש מהם לא יישכח.",
    "tap.progress_note":
      "המחקר נמשך · קרבנות משנים קודמות מתווספים לרישום ככל שמתאמתים מקורות אמינים עליהם.",
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
    "pagination.prev": "הקודם",
    "pagination.next": "הבא",
    "pagination.page_of": "עמוד {page} מתוך {total}",
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
 * Enum translation maps. The data layer keeps canonical English
 * Literal values (``"firearm"``, ``"at_large"``, ``"Haifa District"``);
 * the UI translates them at render time for the chosen language.
 *
 * Schema/source of truth: see ``ExtractedArticleData`` and
 * ``CanonicalCaseSchema`` in ``crime_pipeline/models.py``. Adding a
 * new Literal value there means adding it here too.
 */
type EnumKind =
  | "weapon_type"
  | "suspect_status"
  | "legal_status"
  | "police_investigation_status"
  | "victim_outcome"
  | "district"
  | "exact_place_type"
  | "region"
  | "incident_geography"
  | "incident_type";

const ENUM_TRANSLATIONS: Record<EnumKind, Record<string, Partial<Record<Lang, string>>>> = {
  weapon_type: {
    firearm:    { ar: "سلاح ناري",  he: "כלי ירייה" },
    knife:      { ar: "سكين",       he: "סכין" },
    blunt:      { ar: "أداة حادة",  he: "כלי קהה" },
    explosive:  { ar: "متفجرات",    he: "חומר נפץ" },
    vehicle:    { ar: "مركبة",      he: "כלי רכב" },
    other:      { ar: "أخرى",       he: "אחר" },
    unknown:    { ar: "غير معروف",  he: "לא ידוע" },
  },
  suspect_status: {
    unknown:           { ar: "غير معروف",      he: "לא ידוע" },
    at_large:          { ar: "طليق",            he: "נמלט" },
    wanted:            { ar: "مطلوب",           he: "מבוקש" },
    arrested:          { ar: "معتقل",           he: "נעצר" },
    released_on_bail:  { ar: "أُفرج عنه بكفالة", he: "שוחרר בערבות" },
    in_custody:        { ar: "في الحجز",        he: "במעצר" },
  },
  legal_status: {
    pre_indictment: { ar: "قبل لائحة الاتهام",  he: "טרם הגשת כתב אישום" },
    indicted:       { ar: "تم توجيه اتهام",     he: "הוגש כתב אישום" },
    on_trial:       { ar: "قيد المحاكمة",       he: "במשפט" },
    convicted:      { ar: "أُدين",               he: "הורשע" },
    acquitted:      { ar: "بُرّئ",                he: "זוכה" },
    case_closed:    { ar: "أُغلق الملف",         he: "התיק נסגר" },
  },
  police_investigation_status: {
    open:               { ar: "مفتوح",           he: "פתוח" },
    suspect_identified: { ar: "حُدد المشتبه به",  he: "זוהה חשוד" },
    completed:          { ar: "اكتمل",           he: "הסתיים" },
    indictment_filed:   { ar: "تم رفع لائحة اتهام", he: "הוגש כתב אישום" },
    closed:             { ar: "مغلق",             he: "נסגר" },
  },
  victim_outcome: {
    died:     { ar: "تُوفّي",       he: "נפטר" },
    survived: { ar: "نجا",          he: "שרד" },
    critical: { ar: "حالته حرجة",   he: "מצב אנוש" },
    unknown:  { ar: "غير معروف",    he: "לא ידוע" },
  },
  district: {
    "Northern District":  { ar: "اللواء الشمالي",  he: "מחוז הצפון" },
    "Central District":   { ar: "اللواء المركزي",  he: "מחוז המרכז" },
    "Haifa District":     { ar: "لواء حيفا",       he: "מחוז חיפה" },
    "Tel Aviv District":  { ar: "لواء تل أبيب",    he: "מחוז תל אביב" },
    "Jerusalem District": { ar: "لواء القدس",      he: "מחוז ירושלים" },
    "Southern District":  { ar: "اللواء الجنوبي",  he: "מחוז הדרום" },
  },
  region: {
    Galilee:        { ar: "الجليل",       he: "הגליל" },
    Negev:          { ar: "النقب",        he: "הנגב" },
    Sharon:         { ar: "الشارون",      he: "השרון" },
    Carmel:         { ar: "الكرمل",       he: "הכרמל" },
    "Jordan Valley":{ ar: "غور الأردن",   he: "בקעת הירדן" },
    Triangle:       { ar: "المثلث",        he: "המשולש" },
  },
  exact_place_type: {
    family_home: { ar: "بيت العائلة", he: "בית המשפחה" },
    apartment:   { ar: "شقة",          he: "דירה" },
    street:      { ar: "الشارع",       he: "רחוב" },
    vehicle:     { ar: "مركبة",        he: "כלי רכב" },
    commercial:  { ar: "محل تجاري",    he: "מקום מסחרי" },
    open_area:   { ar: "منطقة مفتوحة", he: "שטח פתוח" },
    school:      { ar: "مدرسة",        he: "בית ספר" },
    other:       { ar: "آخر",          he: "אחר" },
    unknown:     { ar: "غير معروف",    he: "לא ידוע" },
  },
  incident_geography: {
    israel_arab_society:     { ar: "المجتمع العربي في إسرائيل",  he: "החברה הערבית בישראל" },
    israel_jewish_society:   { ar: "المجتمع اليهودي في إسرائيل", he: "החברה היהודית בישראל" },
    israel_other:            { ar: "إسرائيل — أخرى",             he: "ישראל — אחר" },
    palestinian_territories: { ar: "الأراضي الفلسطينية",         he: "השטחים הפלסטיניים" },
    abroad:                  { ar: "خارج البلاد",                he: "בחו״ל" },
    unknown:                 { ar: "غير معروف",                  he: "לא ידוע" },
  },
  incident_type: {
    homicide:           { ar: "جريمة قتل",       he: "רצח" },
    attempted_homicide: { ar: "محاولة قتل",      he: "ניסיון רצח" },
    accident:           { ar: "حادث",             he: "תאונה" },
    suicide:            { ar: "انتحار",            he: "התאבדות" },
    historical:         { ar: "تاريخي",            he: "היסטורי" },
    other_crime:        { ar: "جريمة أخرى",       he: "עבירה אחרת" },
    non_crime:          { ar: "ليس جريمة",        he: "אינו עבירה" },
    unknown:            { ar: "غير معروف",         he: "לא ידוע" },
  },
};

/**
 * Translate an enum value for the requested language. Falls back to
 * the raw value when no translation exists — better to show the
 * English token than an empty string. English (``en``) always
 * returns the raw value because the canonical values are already
 * English-like.
 */
export function translateEnum(
  kind: EnumKind,
  value: string | null | undefined,
  lang: Lang,
): string {
  if (!value) return MISSING;
  if ((lang as string) === "en") return value;
  const v = ENUM_TRANSLATIONS[kind]?.[value]?.[lang];
  return v ?? value;
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

/**
 * Pick the language-specific case narrative for the current UI language,
 * falling back across languages and finally to the legacy single
 * ``case_narrative`` field. Returns null when nothing is available —
 * callers can then choose not to render the summary block at all.
 */
export function pickNarrative(
  ar: string | null | undefined,
  he: string | null | undefined,
  en: string | null | undefined,
  legacy: string | null | undefined,
  lang: Lang,
): string | null {
  const order =
    lang === "ar" ? [ar, he, en] : lang === "he" ? [he, ar, en] : [en, ar, he];
  for (const v of order) {
    if (v && v.trim()) return v;
  }
  if (legacy && legacy.trim()) return legacy;
  return null;
}

/**
 * Pick the city name in the requested language from the gazetteer-
 * normalized record. Falls back to the raw ``city`` field when the
 * gazetteer doesn't know the city or doesn't have the requested
 * script. Unlike names, all gazetteer values are source-attested
 * (hand-curated), so no "inferred" badge is needed — just pick.
 */
export function pickCityLabel(
  raw: string | null | undefined,
  normalized: {
    name_ar?: string | null;
    name_he?: string | null;
    name_en?: string | null;
  } | null | undefined,
  lang: Lang,
): string {
  const target = lang as "ar" | "he" | "en";
  const fromGazetteer =
    target === "ar"
      ? normalized?.name_ar
      : target === "he"
      ? normalized?.name_he
      : normalized?.name_en;
  if (fromGazetteer && fromGazetteer.trim()) return fromGazetteer;
  if (raw && raw.trim()) return raw;
  return MISSING;
}

export interface NameFieldResult {
  value: string;
  /** True when the value came from a source article. False when the
   * value was generated by the deterministic transliterator
   * (``name_transliterations`` entry). */
  isAttested: boolean;
  /** Set only when ``isAttested === false``. */
  sourceScript?: "ar" | "he" | "en";
  /** Set only when ``isAttested === false``. */
  method?: "dictionary" | "rule_based";
  /** True when the field is entirely missing (no source AND no
   * transliteration). UI should render an em-dash or similar. */
  isMissing?: boolean;
}

/**
 * Resolve a name in the chosen language with fallback to the post-merge
 * transliteration. Source-attested values come back with
 * ``isAttested: true``. Inferred values come back with the provenance
 * fields populated so the UI can render an "ⓘ inferred" badge.
 */
export function pickNameWithTransliteration(
  ar: string | null | undefined,
  he: string | null | undefined,
  en: string | null | undefined,
  transliterations: ReadonlyArray<{
    value: string;
    target_script: "ar" | "he" | "en";
    source_script: "ar" | "he" | "en";
    method: "dictionary" | "rule_based";
  }> | undefined | null,
  lang: Lang,
): NameFieldResult {
  // The Lang type doesn't include "en" yet — but the data may. Treat the
  // UI's lang as the target_script directly.
  const target = lang as "ar" | "he" | "en";
  const attested = target === "ar" ? ar : target === "he" ? he : en;
  if (attested && attested.trim()) {
    return { value: attested, isAttested: true };
  }
  const t = (transliterations ?? []).find((x) => x.target_script === target);
  if (t) {
    return {
      value: t.value,
      isAttested: false,
      sourceScript: t.source_script,
      method: t.method,
    };
  }
  return { value: MISSING, isAttested: true, isMissing: true };
}
