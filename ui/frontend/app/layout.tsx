import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Crime Pipeline — Case Browser",
  description: "Review and explore extracted homicide cases",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="he" dir="ltr">
      <body className="min-h-screen bg-background font-sans antialiased">
        <header className="border-b bg-card px-6 py-3 flex items-center gap-3">
          <span className="text-lg font-semibold tracking-tight">Crime Pipeline</span>
          <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
            Case Browser
          </span>
        </header>
        <main className="container mx-auto px-4 py-6 max-w-7xl">{children}</main>
      </body>
    </html>
  );
}
