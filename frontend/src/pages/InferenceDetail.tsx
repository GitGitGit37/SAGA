import { AlertTriangle, ArrowLeft, Bot, History, ShieldAlert, User as UserIcon } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ConfidenceBreakdown } from "@/components/ConfidenceBreakdown";
import { ClaimItem, EvidenceItem } from "@/components/EvidenceList";
import { FeedbackForm } from "@/components/FeedbackForm";
import { ConfidencePill, Empty, ErrorBox, HypothesisBadge, Loading, SafetyBadge, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type Feedback, type FeedbackResult } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { fmtDateTime, humanize, pct } from "@/lib/utils";

function Banner({ tone, icon, children }: { tone: "red" | "amber" | "zinc"; icon: React.ReactNode; children: React.ReactNode }) {
  const cls = { red: "border-red-300 bg-red-50 text-red-800", amber: "border-amber-300 bg-amber-50 text-amber-900", zinc: "border-zinc-300 bg-zinc-100 text-zinc-700" }[tone];
  return <div className={`mb-4 flex gap-3 rounded-lg border p-3 text-sm ${cls}`}><span className="mt-0.5 shrink-0">{icon}</span><div>{children}</div></div>;
}

function SystemReply({ text }: { text: string }) {
  return (
    <div className="mt-2 flex gap-2">
      <div className="grid size-6 shrink-0 place-items-center rounded-full bg-amber-400"><Bot className="size-3.5 text-zinc-950" /></div>
      <div className="whitespace-pre-wrap rounded-lg rounded-tl-none bg-zinc-100 px-3 py-2 text-sm leading-relaxed">{text}</div>
    </div>
  );
}

function FeedbackThread({ items }: { items: Feedback[] }) {
  if (!items.length) return <Empty>No feedback yet.</Empty>;
  return (
    <ul className="space-y-5">
      {items.map((f) => (
        <li key={f.id}>
          <div className="flex gap-2">
            <div className="grid size-6 shrink-0 place-items-center rounded-full bg-zinc-200"><UserIcon className="size-3.5" /></div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5 text-xs text-zinc-500">
                <span className="font-medium text-zinc-800">{f.user.name}</span>
                <span>{humanize(f.user.role)}</span>·<span>{fmtDateTime(f.created_at)}</span>
                <Badge>{humanize(f.feedback_type)}</Badge>
                {f.proposed_hypothesis && <span>→ proposes <b>{humanize(f.proposed_hypothesis)}</b></span>}
                <Badge tone={f.outcome === "applied" ? "ok" : f.outcome === "escalated" ? "danger" : f.outcome === "held" ? "warn" : "neutral"}>{f.outcome}</Badge>
                {f.data_verdict && <Badge tone={f.data_verdict === "confirmed" ? "ok" : "danger"}>later data: {f.data_verdict}</Badge>}
              </div>
              {f.rationale && <p className="mt-1 text-sm">“{f.rationale}”</p>}
              {f.weight != null && (
                <p className="tabular mt-1 text-[11px] text-zinc-500">
                  weight {f.weight.toFixed(3)} = role {f.role_weight} × reliability {f.reliability_at_submission?.toFixed(2)} × (1 − {f.evidence_against?.toFixed(2)} evidence against)
                </p>
              )}
              {f.attached_observation_ids.length > 0 && (
                <p className="mt-1 text-[11px] text-zinc-500">attached records: {f.attached_observation_ids.map((i) => `#${i}`).join(", ")}</p>
              )}
            </div>
          </div>
          {f.system_response && <SystemReply text={f.system_response} />}
        </li>
      ))}
    </ul>
  );
}

export function InferenceDetailPage() {
  const id = Number(useParams().id);
  const { data: d, error, loading, reload } = useLoad(() => api.inference(id), [id]);
  const [last, setLast] = useState<FeedbackResult | null>(null);

  if (error) return <ErrorBox error={error} />;
  if (loading && !d) return <Loading />;
  if (!d) return null;

  const findings = d.evidence.filter((e) => !["llm_claim", "base_rate"].includes(e.kind));
  const supports = findings.filter((e) => e.direction === "supports");
  const contradicts = findings.filter((e) => e.direction === "contradicts");
  const claims = d.evidence.filter((e) => e.kind === "llm_claim");
  const baseRate = d.evidence.find((e) => e.kind === "base_rate");
  const isLatest = d.status !== "superseded";

  return (
    <>
      <Link to={`/assets/${d.asset.asset_tag}`} className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-800">
        <ArrowLeft className="size-3.5" /> <span className="font-mono">{d.asset.asset_tag}</span> memory timeline
      </Link>

      {d.escalated && (
        <Banner tone="red" icon={<ShieldAlert className="size-4" />}>
          <b>Escalated for supervisor review.</b> Someone disagreed with a safety-critical finding. The warning stays active:
          the system does not withdraw a safety warning on the strength of feedback. People decide what to do with the machine.
        </Banner>
      )}
      {d.status === "contested" && !d.escalated && (
        <Banner tone="amber" icon={<AlertTriangle className="size-4" />}>
          <b>Contested.</b> A user disagrees, but their feedback didn't outweigh the data, so the system is holding its
          position. See its reply in the feedback thread.
        </Banner>
      )}
      {!isLatest && (
        <Banner tone="zinc" icon={<History className="size-4" />}>
          You're viewing version {d.version}, which has been superseded.{" "}
          {d.latest_id && <Link className="font-medium underline" to={`/inferences/${d.latest_id}`}>Go to the current version</Link>}
        </Banner>
      )}

      <div className="mb-6">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <StatusBadge inf={d} />
          <HypothesisBadge hypothesis={d.hypothesis} />
          {d.is_safety_critical && <SafetyBadge />}
          <Badge>{humanize(d.category)}</Badge>
          <Badge>{humanize(d.subsystem)}</Badge>
          <Badge>v{d.version}</Badge>
          <span className="text-xs text-zinc-500">by {d.created_by} · data to {fmtDateTime(d.window_end)}</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{d.title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-zinc-700">{d.interpretation}</p>
        {d.recommended_action && (
          <p className="mt-3 max-w-3xl rounded-md border-l-4 border-amber-400 bg-white px-3 py-2 text-sm">
            <span className="font-medium">Recommended:</span> {d.recommended_action}
          </p>
        )}
      </div>

      {last && (
        <Card className="mb-6 border-amber-300">
          <CardHeader>
            <CardTitle>System response to your feedback</CardTitle>
            <CardDescription>
              Outcome: <b>{last.feedback.outcome}</b>
              {last.decision && <> · combined scores {Object.entries(last.decision.combined).map(([h, v]) => `${humanize(h)} ${v.toFixed(2)}`).join(", ")}</>}
              {last.inference.id !== d.id && <> · <Link className="underline" to={`/inferences/${last.inference.id}`}>open new version</Link></>}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <SystemReply text={last.reply.message} />
            {last.reply.data_requests.length > 0 && (
              <div className="mt-3 text-sm">
                <div className="text-xs font-medium text-zinc-500">What would settle it</div>
                <ul className="mt-1 list-disc pl-5">{last.reply.data_requests.map((r, i) => <li key={i}>{r}</li>)}</ul>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Evidence</CardTitle>
              <CardDescription>What the deterministic engine found in the data. Strength is how much each item counts.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <div className="mb-2 text-xs font-medium text-zinc-500">Supports ({supports.length})</div>
                <ul className="space-y-2">{supports.map((e) => <EvidenceItem key={e.id} e={e} />)}</ul>
              </div>
              {contradicts.length > 0 && (
                <div>
                  <div className="mb-2 text-xs font-medium text-zinc-500">Contradicts ({contradicts.length})</div>
                  <ul className="space-y-2">{contradicts.map((e) => <EvidenceItem key={e.id} e={e} />)}</ul>
                </div>
              )}
            </CardContent>
          </Card>

          {claims.length > 0 && d.confidence.weights.llm > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Claude's claims, checked against the data</CardTitle>
                <CardDescription>Unverified claims are dropped and lower Claude's confidence. They never feed the evidence score.</CardDescription>
              </CardHeader>
              <CardContent><ul className="space-y-2">{claims.map((e) => <ClaimItem key={e.id} e={e} />)}</ul></CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Feedback</CardTitle>
              <CardDescription>Every item is weighed as evidence and logged with its weight and outcome.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <FeedbackThread items={d.feedback} />
              {isLatest ? (
                <div className="border-t border-zinc-100 pt-4">
                  <FeedbackForm inf={d} onDone={(r) => { setLast(r); reload(); }} />
                </div>
              ) : (
                <p className="text-sm text-zinc-500">Feedback goes on the current version.</p>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Confidence breakdown</CardTitle></CardHeader>
            <CardContent>
              <ConfidenceBreakdown c={d.confidence} />
              {baseRate && <p className="mt-3 text-[11px] text-zinc-500">History: {baseRate.description}</p>}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Version history</CardTitle><CardDescription>Beliefs are never overwritten</CardDescription></CardHeader>
            <CardContent>
              <ol className="space-y-3">
                {[...d.versions].reverse().map((v) => (
                  <li key={v.id} className={`rounded-md border p-2.5 text-sm ${v.id === d.id ? "border-zinc-900" : "border-zinc-200"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <Link to={`/inferences/${v.id}`} className="font-medium hover:underline">v{v.version} · {humanize(v.hypothesis)}</Link>
                      <ConfidencePill value={v.final_confidence} />
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-zinc-500">
                      <StatusBadge inf={v} /> {v.created_by} · {fmtDateTime(v.created_at)}
                    </div>
                    {v.change_reason && <div className="mt-1 text-[11px] text-zinc-600">{v.change_reason}</div>}
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>

          {d.alternatives.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Alternatives considered</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                {d.alternatives.map((a, i) => (
                  <div key={i} className="text-sm">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{humanize(a.hypothesis)}</span>
                      <span className="tabular text-xs text-zinc-500">{pct(a.confidence.final_confidence)}</span>
                    </div>
                    <div className="text-[11px] text-zinc-500">{a.proposer} · {a.reason}</div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
