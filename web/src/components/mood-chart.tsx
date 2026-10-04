"use client";

import { useState } from "react";

import { moodWord } from "@/lib/format";

export type MoodDay = { day: string; label: string; mood: number | null; notes: string };

const HEIGHT = 140;

/** Seven days of mood, 1-5. One series: one hue, no legend; the title names it. */
export function MoodChart({ days }: { days: MoodDay[] }) {
  const [hover, setHover] = useState<number | null>(null);

  return (
    <figure>
      <div className="flex gap-3">
        <div className="flex flex-col justify-between py-0.5 text-right text-[11px] text-ink-3" style={{ height: HEIGHT }}>
          <span>Very good</span>
          <span>Okay</span>
          <span>Very low</span>
        </div>
        <div className="relative flex flex-1 items-end gap-2 border-b border-line" style={{ height: HEIGHT }}>
          {/* Recessive gridline at "Okay" (3 of 5). */}
          <div className="pointer-events-none absolute inset-x-0 border-t border-dashed border-line" style={{ bottom: HEIGHT / 2 }} />
          {days.map((d, i) => (
            <button
              key={d.day}
              type="button"
              className="relative flex h-full flex-1 items-end justify-center outline-none"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              aria-label={`${d.label}: ${d.mood == null ? "no check-in" : `${moodWord(d.mood)}, ${d.mood} of 5`}`}
            >
              {d.mood == null ? (
                <span className="mb-1 text-xs text-ink-3">–</span>
              ) : (
                <span
                  className="w-full max-w-10 rounded-t-[4px] bg-mood transition-opacity"
                  style={{ height: `${(d.mood / 5) * 100}%`, opacity: hover == null || hover === i ? 1 : 0.45 }}
                />
              )}
              {hover === i && (
                <span className="absolute bottom-full z-10 mb-2 w-max max-w-48 rounded-lg border border-line bg-card px-3 py-2 text-left text-xs shadow-sm">
                  <span className="block font-medium text-ink">{d.label}</span>
                  <span className="block text-ink-2">
                    {d.mood == null ? "No check-in" : `${moodWord(d.mood)} (${d.mood}/5)`}
                  </span>
                  {d.notes && <span className="mt-1 block text-ink-3">“{d.notes}”</span>}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-2 flex gap-2 pl-[4.25rem] text-[11px] text-ink-3">
        {days.map((d) => (
          <span key={d.day} className="flex-1 text-center">
            {d.label.split(" ")[0]}
          </span>
        ))}
      </div>
    </figure>
  );
}
