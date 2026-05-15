"use client";

import Link from "next/link";
import { useLanguage } from "@/lib/language-context";
import { t } from "@/lib/i18n";
import { LanguageToggle } from "@/components/language-toggle";

const CONTACT_URL = "https://www.devfadi.com/#contact";

interface ContributeCard {
  hKey:
    | "contribute.card1.h"
    | "contribute.card2.h"
    | "contribute.card3.h"
    | "contribute.card4.h"
    | "contribute.card5.h"
    | "contribute.card6.h";
  pKey:
    | "contribute.card1.p"
    | "contribute.card2.p"
    | "contribute.card3.p"
    | "contribute.card4.p"
    | "contribute.card5.p"
    | "contribute.card6.p";
}

const CARDS: ContributeCard[] = [
  { hKey: "contribute.card1.h", pKey: "contribute.card1.p" },
  { hKey: "contribute.card2.h", pKey: "contribute.card2.p" },
  { hKey: "contribute.card3.h", pKey: "contribute.card3.p" },
  { hKey: "contribute.card4.h", pKey: "contribute.card4.p" },
  { hKey: "contribute.card5.h", pKey: "contribute.card5.p" },
  { hKey: "contribute.card6.h", pKey: "contribute.card6.p" },
];

export default function ContributePage() {
  const { lang } = useLanguage();

  return (
    <>
      <nav className="top">
        <div className="wrap row">
          <Link href="/" className="brand">
            <img src="/logo.png" alt="" className="brand-mark" aria-hidden="true" />
            <span>{t(lang, "brand")}</span>
          </Link>
          <div className="links" style={{ alignItems: "center" }}>
            <Link href="/">{t(lang, "nav.back_to_register")}</Link>
            <LanguageToggle />
          </div>
        </div>
      </nav>

      <header className="hero">
        <div className="wrap">
          <div className="eyebrow">{t(lang, "contribute.eyebrow")}</div>
          <h1>
            {t(lang, "contribute.h1").split("\n").map((line, i, arr) => (
              <span key={i}>
                {line}
                {i < arr.length - 1 && <br />}
              </span>
            ))}
          </h1>
          <p className="lede">{t(lang, "contribute.lede")}</p>
        </div>
      </header>

      <section className="sec" id="ways">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">{t(lang, "contribute.sec_ways_title")}</h2>
            <div className="sec-meta">{t(lang, "contribute.sec_ways_meta")}</div>
          </div>

          <div className="contribute-grid">
            {CARDS.map((card, i) => (
              <article key={card.hKey} className="contribute-card">
                <div className="contribute-num">
                  {String(i + 1).padStart(2, "0")}
                </div>
                <h3>{t(lang, card.hKey)}</h3>
                <p>{t(lang, card.pKey)}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="sec" id="contact">
        <div className="wrap">
          <div className="sec-head">
            <h2 className="sec-title">{t(lang, "contribute.sec_close_title")}</h2>
          </div>
          <div className="contribute-close">
            <p>{t(lang, "contribute.close_lede")}</p>
            <a
              className="contact-link contribute-close-cta"
              href={CONTACT_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              <span>{t(lang, "contribute.close_cta")}</span>
              <span className="contact-link-arrow" aria-hidden="true">←</span>
            </a>
          </div>
        </div>
      </section>

      <footer className="bottom">
        <div className="wrap">
          <p>{t(lang, "footer.line1")}</p>
        </div>
      </footer>
    </>
  );
}
