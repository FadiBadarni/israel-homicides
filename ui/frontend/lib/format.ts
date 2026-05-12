/**
 * Arabic-language formatting helpers.
 *
 * Dates use Eastern Arabic month names when known; fall back to ISO.
 * Numbers stay as Western digits per the design.
 */

const AR_MONTHS = [
  "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
  "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
];

export function formatArabicDate(iso: string | null): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const year = m[1];
  const month = AR_MONTHS[Number(m[2]) - 1];
  const day = String(Number(m[3]));
  return `${day} ${month} ${year}`;
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
