import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crime Pipeline — Memorial",
  description: "A quiet memorial for victims of homicide in Israel",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" dir="ltr">
      <body className="min-h-screen bg-background font-sans antialiased">
        {children}
      </body>
    </html>
  );
}
