"use client";

import { useState } from "react";

export type LatencyPoint = { at: string; label: string; ms: number; tools: string; flagged: boolean };

const HEIGHT = 120;

/** Latency of recent turns, oldest to newest. One series: one hue, no legend. */
export function LatencyChart({ points, p95 }: { points: LatencyPoint[]; p95: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...points.map((p) => p.ms), p95, 1000);

  return (
    <figure>
      <div className="relative flex items-end gap-[2px] border-b border-line" style={{ height: HEIGHT }}>
        <div
          className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-3"
          style={{ bottom: (p95 / max) * HEIGHT }}
        >
          <span className="absolute -top-4 right-0 text-[11px] text-ink-3">p95 {(p95 / 1000).toFixed(1)}s</span>
        </div>
        {points.map((p, i) => (
          <button
            key={p.at + i}
            type="button"
            className="relative flex h-full flex-1 items-end outline-none"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            onFocus={() => setHover(i)}
            onBlur={() => setHover(null)}
            aria-label={`${p.label}: ${(p.ms / 1000).toFixed(1)} seconds`}
          >
            <span
              className="w-full rounded-t-[4px] bg-mood"
              style={{ height: `${Math.max((p.ms / max) * 100, 2)}%`, opacity: hover == null || hover === i ? 1 : 0.45 }}
            />
            {hover === i && (
              <span className="absolute bottom-full left-1/2 z-10 mb-2 w-max max-w-56 -translate-x-1/2 rounded-lg border border-line bg-card px-3 py-2 text-left text-xs shadow-sm">
                <span className="block font-medium text-ink">{(p.ms / 1000).toFixed(2)} s</span>
                <span className="block text-ink-2">{p.label}</span>
                <span className="block text-ink-3">{p.tools || "no tools"}</span>
                {p.flagged && <span className="block text-ink-2">Guardrail stepped in</span>}
              </span>
            )}
          </button>
        ))}
      </div>
      <figcaption className="mt-2 flex justify-between text-[11px] text-ink-3">
        <span>Older</span>
        <span>Latest turn</span>
      </figcaption>
    </figure>
  );
}
