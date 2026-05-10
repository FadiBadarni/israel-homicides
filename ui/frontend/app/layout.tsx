import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crime Pipeline — Case Browser",
  description: "Review and explore extracted homicide cases",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" dir="ltr">
      <body className="min-h-screen bg-background font-sans antialiased">
        <header className="border-b bg-card px-6 py-3 flex items-center gap-6">
          <span className="text-lg font-semibold tracking-tight">Crime Pipeline</span>
          <nav className="flex items-center gap-4 text-sm">
            <Link href="/cases" className="text-muted-foreground hover:text-foreground transition-colors">
              Cases
            </Link>
            <Link href="/review" className="text-muted-foreground hover:text-foreground transition-colors">
              Review queue
            </Link>
          </nav>
        </header>
        <main className="container mx-auto px-4 py-6 max-w-7xl">{children}</main>
      </body>
    </html>
  );
}
