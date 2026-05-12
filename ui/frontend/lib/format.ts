import type { Lang } from "./language-context";

/**
 * Bilingual date formatting helpers.
 *
 * Arabic uses Eastern Arabic month names; Hebrew uses Hebrew calendar
 * month names with the "ב" prefix preposition. Numbers stay as Western
 * digits per the design.
 */

const AR_MONTHS = [
  "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
  "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
];

const HE_MONTHS = [
  "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
  "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
];

export function formatDate(iso: string | null, lang: Lang = "ar"): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const year = m[1];
  const monthIdx = Number(m[2]) - 1;
  const day = String(Number(m[3]));
  if (lang === "he") {
    return `${day} ב${HE_MONTHS[monthIdx]} ${year}`;
  }
  return `${day} ${AR_MONTHS[monthIdx]} ${year}`;
}

// Backward-compat shim — calls formatDate with "ar"
export function formatArabicDate(iso: string | null): string {
  return formatDate(iso, "ar");
}

export function yearOf(iso: string | null): number | null {
  if (!iso) return null;
  const y = Number(iso.slice(0, 4));
  return isNaN(y) ? null : y;
}

export function pluralVictims(n: number): string {
  // Arabic plural is complex; for a memorial register we use "ضحيّة" uniformly.
  return n === 1 ? "ضحيّة واحدة" : `${n} ضحيّة`;
}
