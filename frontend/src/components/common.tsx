import { AlertTriangle, CheckCircle2, CircleDot, History, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import type { InferenceBrief } from "@/lib/api";
import { cn, humanize, pct } from "@/lib/utils";

export function HealthBadge({ health }: { health: "ok" | "warning" | "critical" }) {
  if (health === "critical")
    return (
      <Badge tone="danger">
        <ShieldAlert /> Critical
      </Badge>
    );
  if (health === "warning")
    return (
      <Badge tone="warn">
        <AlertTriangle /> Attention
      </Badge>
    );
  return (
    <Badge tone="ok">
      <CheckCircle2 /> Healthy
    </Badge>
  );
}

export function StatusBadge({ inf }: { inf: Pick<InferenceBrief, "status" | "escalated"> }) {
  if (inf.escalated)
    return (
      <Badge tone="danger">
        <ShieldAlert /> Escalated
      </Badge>
    );
  switch (inf.status) {
    case "contested":
      return (
        <Badge tone="warn">
          <AlertTriangle /> Contested
        </Badge>
      );
    case "confirmed":
      return (
        <Badge tone="ok">
          <CheckCircle2 /> Confirmed
        </Badge>
      );
    case "superseded":
      return (
        <Badge tone="neutral">
          <History /> Superseded
        </Badge>
      );
    default:
      return (
        <Badge tone="info">
          <CircleDot /> Active
        </Badge>
      );
  }
}

export function SafetyBadge() {
  return (
    <Badge tone="dark">
      <ShieldAlert className="text-amber-400" /> Safety-critical
    </Badge>
  );
}

const HYPOTHESIS_TONE: Record<string, "danger" | "warn" | "violet" | "info" | "ok" | "neutral"> = {
  acute_failure: "danger",
  degradation: "warn",
  sensor_fault: "violet",
  operating_practice: "info",
  resolved: "ok",
  environmental: "neutral",
};

export function HypothesisBadge({ hypothesis }: { hypothesis: string }) {
  return <Badge tone={HYPOTHESIS_TONE[hypothesis] ?? "neutral"}>{humanize(hypothesis)}</Badge>;
}

export function ConfidencePill({ value, className }: { value: number; className?: string }) {
  const tone = value >= 0.7 ? "bg-zinc-900 text-white" : value >= 0.5 ? "bg-zinc-600 text-white" : "bg-zinc-200 text-zinc-700";
  return (
    <span className={cn("tabular rounded px-1.5 py-0.5 text-xs font-semibold", tone, className)}>{pct(value)}</span>
  );
}

export function Meter({ value, tone = "zinc", className }: { value: number; tone?: "zinc" | "emerald" | "red" | "amber"; className?: string }) {
  const color = { zinc: "bg-zinc-800", emerald: "bg-emerald-500", red: "bg-red-500", amber: "bg-amber-500" }[tone];
  return (
    <div className={cn("h-1.5 w-full overflow-hidden rounded-full bg-zinc-200", className)}>
      <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-zinc-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="py-16 text-center text-sm text-zinc-500">{label}</div>;
}

export function ErrorBox({ error }: { error: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      Couldn't load: {error}. Is the backend running on port 8000?
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500">{children}</div>;
}
