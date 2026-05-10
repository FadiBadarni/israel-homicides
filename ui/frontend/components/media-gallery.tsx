"use client";

import { useState } from "react";
import type { MediaItem } from "@/lib/api";
import { cn } from "@/lib/utils";

interface MediaGalleryProps {
  items: MediaItem[];
  label?: string;
}

export function MediaGallery({ items, label }: MediaGalleryProps) {
  const [enlarged, setEnlarged] = useState<MediaItem | null>(null);

  if (!items.length) {
    return (
      <div className="rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground text-center">
        No media available
      </div>
    );
  }

  return (
    <div>
      {label && <h3 className="text-sm font-semibold mb-2">{label}</h3>}
      <div className="flex flex-wrap gap-2">
        {items.map((item, i) => (
          <button
            key={item.sha256 ?? item.url ?? i}
            onClick={() => setEnlarged(item)}
            className={cn(
              "relative group rounded-md overflow-hidden border bg-muted",
              "w-24 h-24 flex-shrink-0 hover:ring-2 hover:ring-primary transition-all"
            )}
            title={item.classification ?? undefined}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={item.url}
              alt={item.classification ?? "media"}
              className="w-full h-full object-cover"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            {item.classification && (
              <span className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-[10px] px-1 py-0.5 truncate">
                {item.classification}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Lightbox */}
      {enlarged && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setEnlarged(null)}
        >
          <div
            className="bg-card rounded-xl shadow-2xl max-w-3xl w-full p-4 space-y-3"
            onClick={(e) => e.stopPropagation()}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={enlarged.url}
              alt={enlarged.classification ?? "media"}
              className="w-full rounded-lg object-contain max-h-[60vh]"
            />
            <div className="text-sm space-y-1">
              {enlarged.classification && (
                <p><span className="font-medium">Class:</span> {enlarged.classification}</p>
              )}
              {enlarged.confidence !== null && enlarged.confidence !== undefined && (
                <p><span className="font-medium">Confidence:</span> {Math.round(enlarged.confidence * 100)}%</p>
              )}
              {enlarged.width && enlarged.height && (
                <p><span className="font-medium">Dims:</span> {enlarged.width}×{enlarged.height}</p>
              )}
              {enlarged.evidence?.length ? (
                <p><span className="font-medium">Evidence:</span> {enlarged.evidence.join(", ")}</p>
              ) : null}
              <a
                href={enlarged.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-blue-600 underline break-all block"
              >
                {enlarged.url}
              </a>
            </div>
            <button
              onClick={() => setEnlarged(null)}
              className="w-full border rounded-md py-1.5 text-sm hover:bg-muted"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
