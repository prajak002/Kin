export const MOOD_WORDS = ["", "Very low", "Low", "Okay", "Good", "Very good"];

export function moodWord(mood: number | null | undefined): string {
  if (mood == null) return "No check-in";
  return MOOD_WORDS[Math.round(mood)] ?? String(mood);
}

export function age(birthYear: number): number {
  return new Date().getFullYear() - birthYear;
}

export function timeAgo(iso: string, now = Date.now()): string {
  const mins = Math.round((now - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

export function dayKey(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleDateString("en-CA"); // YYYY-MM-DD in local time
}
