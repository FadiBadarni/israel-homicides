"use client";

import Link from "next/link";
import { useLanguage } from "@/lib/language-context";
import { t } from "@/lib/i18n";
import { LanguageToggle } from "@/components/language-toggle";

const CONTACT_URL = "https://www.devfadi.com/#contact";

interface ContributeRow {
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

const ROWS: ContributeRow[] = [
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

      {/* Asymmetric editorial hero — h1 in one column, lede in the other,
          separated by a quiet vertical rule. Distinct from the home page's
          centered-everything hero. */}
      <header className="contribute-hero">
        <div className="wrap">
          <div className="eyebrow">{t(lang, "contribute.eyebrow")}</div>
          <div className="contribute-hero-grid">
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
        </div>
      </header>

      {/* Six numbered rows, narrative-style. The Amiri "01"–"06" anchors
          the page typographically; each row reads like an entry in a
          manifesto rather than a marketing tile. */}
      <section className="contribute-rows" id="ways">
        <div className="wrap">
          {ROWS.map((row, i) => (
            <article key={row.hKey} className="contribute-row">
              <div className="contribute-row-num" aria-hidden="true">
                {String(i + 1).padStart(2, "0")}
              </div>
              <div className="contribute-row-body">
                <h3>{t(lang, row.hKey)}</h3>
                <p>{t(lang, row.pKey)}</p>
              </div>
            </article>
          ))}
        </div>
      </section>

      {/* Closing P.S. — a single hairline rule and a centered invitation.
          Quieter than a sectioned CTA; reads as a postscript. */}
      <section className="contribute-postscript" id="contact">
        <div className="wrap">
          <div className="contribute-postscript-rule" aria-hidden="true" />
          <p>{t(lang, "contribute.close_lede")}</p>
          <a
            className="contact-link"
            href={CONTACT_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            <span>{t(lang, "contribute.close_cta")}</span>
            <span className="contact-link-arrow" aria-hidden="true">←</span>
          </a>
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
