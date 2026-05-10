"use client";

interface BidiNameProps {
  he?: string | null;
  ar?: string | null;
  en?: string | null;
  className?: string;
}

/**
 * Renders victim name fields with correct bidirectional text isolation.
 * Uses <bdi> so each script runs in its own bidi context without bleeding
 * into surrounding LTR layout.
 */
export function BidiName({ he, ar, en, className }: BidiNameProps) {
  const primary = he || ar || en;
  if (!primary) {
    return <span className={`text-muted-foreground italic ${className ?? ""}`}>Unknown</span>;
  }

  return (
    <span className={className}>
      <bdi dir="auto">{primary}</bdi>
      {he && ar && (
        <span className="text-muted-foreground text-xs ml-1.5">
          (<bdi dir="auto">{ar}</bdi>)
        </span>
      )}
    </span>
  );
}
