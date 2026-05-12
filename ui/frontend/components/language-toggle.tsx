"use client";

import { useLanguage } from "@/lib/language-context";

export function LanguageToggle() {
  const { lang, setLang } = useLanguage();
  return (
    <div className="lang-toggle" role="group" aria-label="Language">
      <button
        type="button"
        className={lang === "ar" ? "on" : ""}
        onClick={() => setLang("ar")}
        aria-pressed={lang === "ar"}
      >
        عربي
      </button>
      <button
        type="button"
        className={lang === "he" ? "on" : ""}
        onClick={() => setLang("he")}
        aria-pressed={lang === "he"}
      >
        עברית
      </button>
    </div>
  );
}
