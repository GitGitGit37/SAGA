import { AlertTriangle, ChevronRight, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { ConfidencePill, ErrorBox, HealthBadge, Loading, PageHeader, StatusBadge } from "@/components/common";
import { Card } from "@/components/ui/card";
import { api, type AssetSummary } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { fmtDate, humanize } from "@/lib/utils";

const HEALTH_ORDER = { critical: 0, warning: 1, ok: 2 };

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`tabular mt-0.5 text-2xl font-semibold ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

function AssetCard({ a }: { a: AssetSummary }) {
  const top = a.inferences.filter((i) => i.hypothesis !== "resolved").slice(0, 2);
  const resolved = a.inferences.filter((i) => i.hypothesis === "resolved");
  return (
    <Link to={`/assets/${a.asset_tag}`} className="group min-w-0">
      <Card
        className={`h-full p-4 transition-shadow group-hover:shadow-md ${
          a.health === "critical" ? "border-red-300" : a.health === "warning" ? "border-amber-200" : ""
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="font-mono text-base font-semibold">{a.asset_tag}</div>
            <div className="text-xs text-zinc-500">
              {a.name} · {humanize(a.asset_type)}
            </div>
          </div>
          <HealthBadge health={a.health} />
        </div>

        <div className="mt-3 space-y-2">
          {top.length === 0 && resolved.length === 0 && (
            <p className="text-xs text-zinc-500">No active findings.</p>
          )}
          {top.map((i) => (
            <div key={i.id} className="flex items-start gap-2 text-sm">
              {i.is_safety_critical ? (
                <ShieldAlert className="mt-0.5 size-4 shrink-0 text-red-600" />
              ) : (
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-500" />
              )}
              <span className="flex-1 leading-snug">{i.title}</span>
              <ConfidencePill value={i.final_confidence} />
            </div>
          ))}
          {resolved.map((i) => (
            <div key={i.id} className="text-xs text-emerald-700">
              ✓ {i.title}
            </div>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {a.inferences.slice(0, 3).map((i) => (
            <StatusBadge key={i.id} inf={i} />
          ))}
        </div>

        {a.recent_alerts.length > 0 && (
          <div className="mt-3 border-t border-zinc-100 pt-2">
            {a.recent_alerts.slice(0, 2).map((al, idx) => (
              <div key={idx} className="flex min-w-0 items-center gap-2 text-xs text-zinc-600">
                <span className="shrink-0 whitespace-nowrap font-mono font-semibold">{al.code}</span>
                <span className="min-w-0 truncate">{al.description}</span>
                <span className="ml-auto shrink-0 text-zinc-400">{fmtDate(al.observed_at)}</span>
              </div>
            ))}
          </div>
        )}
        <div className="mt-3 flex items-center text-xs font-medium text-zinc-400 group-hover:text-zinc-700">
          Memory timeline <ChevronRight className="size-3.5" />
        </div>
      </Card>
    </Link>
  );
}

export function FleetDashboard() {
  const { data, error, loading } = useLoad(api.assets, []);
  if (error) return <ErrorBox error={error} />;
  if (loading || !data) return <Loading />;

  const sites = [...new Set(data.map((a) => a.site))];
  const sorted = (xs: AssetSummary[]) => [...xs].sort((a, b) => HEALTH_ORDER[a.health] - HEALTH_ORDER[b.health]);
  const totals = data.reduce(
    (t, a) => ({
      critical: t.critical + (a.health === "critical" ? 1 : 0),
      warning: t.warning + (a.health === "warning" ? 1 : 0),
      contested: t.contested + a.counts.contested,
      escalated: t.escalated + a.counts.escalated,
    }),
    { critical: 0, warning: 0, contested: 0, escalated: 0 },
  );

  return (
    <>
      <PageHeader title="Fleet" subtitle="Every machine's current beliefs, with how sure the system is and whether anyone disagrees." />
      <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Machines" value={data.length} />
        <Stat label="Critical" value={totals.critical} tone="text-red-600" />
        <Stat label="Need attention" value={totals.warning} tone="text-amber-600" />
        <Stat label="Contested" value={totals.contested} />
        <Stat label="Escalated" value={totals.escalated} tone="text-red-600" />
      </div>
      {sites.map((site) => (
        <section key={site} className="mb-8">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">{site}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {sorted(data.filter((a) => a.site === site)).map((a) => (
              <AssetCard key={a.id} a={a} />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
