import type { Alert } from "@/lib/kin";

const ALERT_STYLE: Record<Alert["level"], { icon: string; label: string; color: string }> = {
  urgent: { icon: "▲", label: "Urgent", color: "var(--critical)" },
  warning: { icon: "●", label: "Needs attention", color: "var(--warning)" },
  info: { icon: "○", label: "Note", color: "var(--ink-3)" },
};

// Status colour never carries meaning alone: icon + label + colour.
export function AlertBadge({ level }: { level: Alert["level"] }) {
  const s = ALERT_STYLE[level];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-2">
      <span aria-hidden style={{ color: s.color }}>
        {s.icon}
      </span>
      {s.label}
    </span>
  );
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line bg-card p-5 ${className}`}>{children}</section>;
}

export function Offline({ message }: { message: string }) {
  return (
    <Card>
      <h2 className="font-display text-xl">Kin isn&apos;t running yet</h2>
      <p className="mt-2 text-sm text-ink-2">{message}</p>
      <p className="mt-4 text-sm text-ink-2">
        Start everything from the project folder with <code className="rounded bg-accent-soft px-1.5 py-0.5">scripts/dev.sh</code>
      </p>
    </Card>
  );
}
