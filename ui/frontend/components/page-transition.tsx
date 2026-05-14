"use client";

import { usePathname } from "next/navigation";

/**
 * Per-route fade-in wrapper. Keyed on the pathname so React unmounts and
 * remounts the wrapper on every client-side navigation, restarting the CSS
 * animation. No motion library, no JS animation loop — the actual fade is
 * driven entirely by the .page-transition CSS rule in globals.css, which
 * also honors prefers-reduced-motion.
 */
export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div key={pathname} className="page-transition">
      {children}
    </div>
  );
}
