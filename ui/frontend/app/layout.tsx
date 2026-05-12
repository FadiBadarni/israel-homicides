import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "سجل ضحايا الجريمة في المجتمع العربي",
  description: "سجلٌّ عامّ يوثّق ضحايا جرائم القتل في المجتمع العربي في إسرائيل، اسماً تلو الآخر.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@400;500;600;700&family=Amiri:wght@400;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
