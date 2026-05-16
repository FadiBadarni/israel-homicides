import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";
import { LanguageProvider } from "@/lib/language-context";
import { PageTransition } from "@/components/page-transition";

export const metadata: Metadata = {
  title: "سجل ضحايا الجريمة في المجتمع العربي",
  description: "سجلٌّ عامّ يوثّق ضحايا جرائم القتل في المجتمع العربي في إسرائيل، اسماً تلو الآخر.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@400;500;600;700&family=Amiri:wght@400;700&family=Frank+Ruhl+Libre:wght@400;500;700&family=Noto+Sans+Hebrew:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <LanguageProvider>
          <PageTransition>{children}</PageTransition>
        </LanguageProvider>
        <Analytics />
      </body>
    </html>
  );
}
