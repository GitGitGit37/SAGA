import { AlertOctagon, ArrowLeft, Brain, ClipboardList, MessageCircle, RefreshCw, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  ConfidencePill, Empty, ErrorBox, HypothesisBadge, Loading, PageHeader, SafetyBadge, StatusBadge,
} from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type AssetDetail, type TimelineEvent } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { cn, fmtDate, fmtDateTime, humanize, pct } from "@/lib/utils";

const METRICS = [
  { key: "coolant_temp_max_c", label: "Coolant temp (max)", unit: "°C" },
  { key: "hydraulic_pressure_bar", label: "Hydraulic pressure", unit: "bar" },
  { key: "hydraulic_oil_temp_c", label: "Hydraulic oil temp", unit: "°C" },
  { key: "idle_pct", label: "Idle time", unit: "%" },
  { key: "fuel_rate_lph", label: "Fuel rate", unit: "L/h" },
  { key: "oil_pressure_kpa", label: "Oil pressure", unit: "kPa" },
];
const LINE_COLORS = ["#d97706", "#2563eb", "#059669", "#7c3aed", "#dc2626"];

function defaultMetric(d: AssetDetail): string {
  const sub = d.live_inferences[0]?.subsystem;
  if (sub === "hydraulics") return "hydraulic_pressure_bar";
  if (sub === "operator") return "idle_pct";
  return "coolant_temp_max_c";
}

function TelemetryChart({ d }: { d: AssetDetail }) {
  const [metric, setMetric] = useState(() => defaultMetric(d));
  const [showAmbient, setShowAmbient] = useState(false);
  const m = METRICS.find((x) => x.key === metric)!;
  const limit = d.limits[metric];
  const rows = useMemo(() => d.telematics.map((r) => ({ ...r, ts: new Date(r.t).getTime() })), [d.telematics]);

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <div>
          <CardTitle>Telemetry</CardTitle>
          <CardDescription>Shift-level readings with warning and critical limits</CardDescription>
        </div>
        <div className="flex flex-wrap gap-1">
          {METRICS.map((x) => (
            <button
              key={x.key}
              onClick={() => setMetric(x.key)}
              className={cn(
                "rounded-md px-2 py-1 text-xs",
                metric === x.key ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100",
              )}
            >
              {x.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#e4e4e7" strokeDasharray="3 3" />
              <XAxis
                dataKey="ts"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v) => fmtDate(new Date(v).toISOString())}
                tick={{ fontSize: 11, fill: "#71717a" }}
              />
              <YAxis tick={{ fontSize: 11, fill: "#71717a" }} domain={["auto", "auto"]} />
              <Tooltip
                labelFormatter={(v) => fmtDateTime(new Date(v as number).toISOString())}
                formatter={(v: number, name) => [`${v} ${name === "ambient_temp_c" ? "°C" : m.unit}`, name === "ambient_temp_c" ? "Ambient" : m.label]}
              />
              {limit && <ReferenceLine y={limit.warn} stroke="#f59e0b" strokeDasharray="4 4" label={{ value: "warn", fontSize: 10, fill: "#b45309", position: "insideTopRight" }} />}
              {limit && <ReferenceLine y={limit.critical} stroke="#dc2626" strokeDasharray="4 4" label={{ value: "critical", fontSize: 10, fill: "#b91c1c", position: "insideTopRight" }} />}
              <Line type="monotone" dataKey={metric} stroke="#18181b" strokeWidth={1.5} dot={false} isAnimationActive={false} />
              {showAmbient && (
                <Line type="monotone" dataKey="ambient_temp_c" stroke="#38bdf8" strokeWidth={1} dot={false} isAnimationActive={false} />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <label className="mt-2 flex items-center gap-2 text-xs text-zinc-600">
          <input type="checkbox" checked={showAmbient} onChange={(e) => setShowAmbient(e.target.checked)} />
          Overlay ambient temperature
        </label>
      </CardContent>
    </Card>
  );
}

function ConfidenceTrend({ d }: { d: AssetDetail }) {
  const lineages = [...new Set(d.confidence_trend.map((p) => p.lineage_id))];
  if (!lineages.length) return null;
  // One row per version event; each lineage is its own series.
  const rows = d.confidence_trend.map((p) => ({ ts: new Date(p.at).getTime(), [p.lineage_id]: p.final_confidence, ...p }));
  const label = (id: string) => {
    const last = d.confidence_trend.filter((p) => p.lineage_id === id).at(-1)!;
    return `${humanize(last.subsystem)} (${humanize(last.hypothesis)})`;
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Confidence over time</CardTitle>
        <CardDescription>Each line is one belief; points are versions</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#e4e4e7" strokeDasharray="3 3" />
              <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin - 3600000", "dataMax + 3600000"]}
                tickFormatter={(v) => fmtDateTime(new Date(v).toISOString())} tick={{ fontSize: 10, fill: "#71717a" }} />
              <YAxis domain={[0, 1]} tickFormatter={(v) => pct(v)} tick={{ fontSize: 11, fill: "#71717a" }} />
              <Tooltip labelFormatter={(v) => fmtDateTime(new Date(v as number).toISOString())}
                formatter={(v: number, id) => [pct(v), label(String(id))]} />
              <Legend formatter={(id) => label(String(id))} wrapperStyle={{ fontSize: 11 }} />
              {lineages.map((id, i) => (
                <Line key={id} dataKey={id} stroke={LINE_COLORS[i % LINE_COLORS.length]} connectNulls
                  dot={{ r: 4 }} strokeWidth={2} isAnimationActive={false} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

const FILTERS = ["all", "inference", "feedback", "notes", "fault_code"] as const;

function eventMatches(e: TimelineEvent, f: (typeof FILTERS)[number]) {
  if (f === "all") return true;
  if (f === "notes") return e.type === "maintenance_note" || e.type === "inspection_note";
  return e.type === f;
}

function EventRow({ e }: { e: TimelineEvent }) {
  if (e.type === "inference" && "inference" in e) {
    const i = e.inference;
    return (
      <Row icon={<Brain className="size-4 text-amber-600" />} at={e.at} kind={`Inference v${i.version}`}>
        <Link to={`/inferences/${i.id}`} className="font-medium hover:underline">{i.title}</Link>
        <div className="mt-1 flex flex-wrap gap-1.5">
          <StatusBadge inf={i} /> <HypothesisBadge hypothesis={i.hypothesis} /> <ConfidencePill value={i.final_confidence} />
          <span className="text-xs text-zinc-500">by {i.created_by}</span>
        </div>
      </Row>
    );
  }
  if (e.type === "feedback" && "feedback" in e) {
    const f = e.feedback;
    return (
      <Row icon={<MessageCircle className="size-4 text-sky-600" />} at={e.at} kind={`Feedback · ${humanize(f.feedback_type)}`}>
        <div className="text-sm">
          <span className="font-medium">{f.user.name}</span>{" "}
          <span className="text-zinc-500">({humanize(f.user.role)})</span>
          {f.rationale && <>: “{f.rationale}”</>}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
          <Badge tone={f.outcome === "applied" ? "ok" : f.outcome === "escalated" ? "danger" : f.outcome === "held" ? "warn" : "neutral"}>
            {f.outcome}
          </Badge>
          {f.weight != null && <span className="tabular">weight {f.weight.toFixed(3)}</span>}
          <Link className="underline" to={`/inferences/${f.inference_id}`}>view</Link>
        </div>
      </Row>
    );
  }
  if ("observation" in e) {
    const o = e.observation;
    const isFault = o.kind === "fault_code";
    return (
      <Row
        icon={isFault ? <AlertOctagon className="size-4 text-red-500" /> : o.kind === "maintenance_note" ? <Wrench className="size-4 text-zinc-600" /> : <ClipboardList className="size-4 text-zinc-600" />}
        at={e.at}
        kind={humanize(o.kind)}
      >
        <div className="text-sm">{isFault ? <span className="font-mono font-semibold">{String(o.payload.code)}</span> : o.text}</div>
        {o.category && (
          <div className="mt-1 text-xs text-zinc-500">
            categorised <span className="font-medium text-zinc-700">{humanize(o.category)}</span>
            {o.category_confidence != null && <> ({pct(o.category_confidence)})</>} · #{o.id}
          </div>
        )}
      </Row>
    );
  }
  return null;
}

function Row({ icon, at, kind, children }: { icon: React.ReactNode; at: string; kind: string; children: React.ReactNode }) {
  return (
    <li className="relative flex gap-3 pb-5 last:pb-0">
      <div className="z-10 grid size-8 shrink-0 place-items-center rounded-full border border-zinc-200 bg-white">{icon}</div>
      <div className="min-w-0 flex-1 pt-1">
        <div className="text-xs text-zinc-500">
          {kind} · {fmtDateTime(at)}
        </div>
        <div className="mt-0.5">{children}</div>
      </div>
    </li>
  );
}

export function AssetTimeline() {
  const { tag = "" } = useParams();
  const { data: d, error, loading, reload } = useLoad(() => api.asset(tag), [tag]);
  const { meta } = useSession();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const [running, setRunning] = useState(false);
  const [runMsg, setRunMsg] = useState<string | null>(null);

  if (error) return <ErrorBox error={error} />;
  if (loading && !d) return <Loading />;
  if (!d) return null;

  const analyze = async () => {
    setRunning(true);
    setRunMsg(null);
    try {
      const r = await api.analyze(tag);
      setRunMsg(r.created.length ? `${r.created.length} new or revised inference(s).` : "No change: memory already reflects the data.");
      reload();
    } catch (e) {
      setRunMsg((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const events = d.events.filter((e) => eventMatches(e, filter));
  return (
    <>
      <Link to="/" className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-800">
        <ArrowLeft className="size-3.5" /> Fleet
      </Link>
      <PageHeader
        title={<span className="font-mono">{d.asset_tag}</span>}
        subtitle={`${d.name} · ${humanize(d.asset_type)} · ${d.site} · S/N ${d.serial_number}`}
        actions={
          <Button onClick={analyze} disabled={running} variant="brand">
            <RefreshCw className={running ? "animate-spin" : ""} />
            {running ? "Analysing…" : meta?.llm_enabled ? "Re-analyse with Claude" : "Re-analyse"}
          </Button>
        }
      />
      {runMsg && <div className="mb-4 rounded-md border border-zinc-200 bg-white p-3 text-sm">{runMsg}</div>}

      <div className="mb-6 grid gap-4 md:grid-cols-2">
        {d.live_inferences.length === 0 && <Empty>No current findings for this machine.</Empty>}
        {d.live_inferences.map((i) => (
          <Link key={i.id} to={`/inferences/${i.id}`}>
            <Card className="h-full p-4 hover:shadow-md">
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium leading-snug">{i.title}</div>
                <ConfidencePill value={i.final_confidence} />
              </div>
              <p className="mt-2 line-clamp-3 text-sm text-zinc-600">{i.interpretation}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                <StatusBadge inf={i} /> <HypothesisBadge hypothesis={i.hypothesis} />
                {i.is_safety_critical && <SafetyBadge />}
                <Badge>{humanize(i.subsystem)}</Badge>
              </div>
            </Card>
          </Link>
        ))}
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <TelemetryChart d={d} />
        </div>
        <ConfidenceTrend d={d} />
      </div>

      <Card>
        <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>Memory timeline</CardTitle>
            <CardDescription>Observations, inferences, repairs and feedback, newest first</CardDescription>
          </div>
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button key={f} onClick={() => setFilter(f)}
                className={cn("rounded-md px-2 py-1 text-xs", filter === f ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100")}>
                {f === "fault_code" ? "faults" : f}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <Empty>Nothing here yet.</Empty>
          ) : (
            <ol className="relative before:absolute before:left-4 before:top-2 before:bottom-2 before:w-px before:bg-zinc-200">
              {events.slice(0, 80).map((e, idx) => (
                <EventRow key={idx} e={e} />
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </>
  );
}
