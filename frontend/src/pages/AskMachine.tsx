import { Bot, ChevronDown, Send, User as UserIcon } from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/form";
import { api, type Memory, type RecallResult } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { fmtDate, humanize, llmLabel } from "@/lib/utils";

const EXAMPLES = [
  "What's been going on with excavator 320-A's hydraulics?",
  "Why is EX-320-A running hot?",
  "Which machines have safety issues?",
  "What happened to the radiator on WL-966-B?",
  "Is anything contested right now?",
];

type Turn = { q: string; r?: RecallResult; error?: string };

function memoryLink(ref: string): string | null {
  return ref.startsWith("I") ? `/inferences/${ref.slice(1)}` : null;
}

function AnswerText({ text, memories }: { text: string; memories: Memory[] }) {
  const byRef = Object.fromEntries(memories.map((m) => [m.ref, m]));
  const parts = text.split(/(\[[IO]\d+\])/g);
  return (
    <div className="whitespace-pre-wrap text-sm leading-relaxed">
      {parts.map((p, i) => {
        const m = p.match(/^\[([IO]\d+)\]$/);
        if (!m) return <Fragment key={i}>{p}</Fragment>;
        const ref = m[1];
        const href = memoryLink(ref);
        const mem = byRef[ref];
        const cls = "mx-0.5 rounded bg-amber-100 px-1 font-mono text-[11px] font-semibold text-amber-900 hover:bg-amber-200";
        return href ? (
          <Link key={i} to={href} className={cls} title={mem?.content}>{ref}</Link>
        ) : (
          <span key={i} className={cls} title={mem?.content}>{ref}</span>
        );
      })}
    </div>
  );
}

function Memories({ r }: { r: RecallResult }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3 border-t border-zinc-100 pt-2">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-800">
        <ChevronDown className={`size-3 transition-transform ${open ? "" : "-rotate-90"}`} />
        {r.memories.length} memories retrieved
        {r.filters.asset_tags.length > 0 && <> · asset {r.filters.asset_tags.join(", ")}</>}
        {r.filters.subsystem && <> · {humanize(r.filters.subsystem)}</>}
        {r.filters.safety_only && <> · safety only</>}
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5">
          {r.memories.map((m) => (
            <li key={m.ref} className={`rounded border p-2 text-xs ${r.cited.includes(m.ref) ? "border-amber-300 bg-amber-50" : "border-zinc-200"}`}>
              <div className="flex items-center gap-2 text-zinc-500">
                <span className="font-mono font-semibold text-zinc-800">{m.ref}</span>
                <span>{m.asset_tag}</span>
                <span>{m.owner_type === "inference" ? String(m.meta.status) : humanize(String(m.meta.kind))}</span>
                <span>{fmtDate(m.when)}</span>
                <span className="tabular ml-auto">sim {m.similarity.toFixed(2)}</span>
              </div>
              <div className="mt-1 text-zinc-700">{m.content}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AskMachine() {
  const assets = useLoad(api.assets, []);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [assetTag, setAssetTag] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const ask = async (question: string) => {
    if (!question.trim()) return;
    setQ("");
    setBusy(true);
    setTurns((t) => [...t, { q: question }]);
    try {
      const r = await api.recall({ question, asset_tag: assetTag || undefined });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, r } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Ask the machine" subtitle="Answers come only from stored memories and cite them. If memory doesn't cover it, it says so." />
      <div className="flex min-h-[60vh] flex-col rounded-lg border border-zinc-200 bg-white">
        <div className="flex-1 space-y-6 p-4">
          {turns.length === 0 && (
            <div className="py-8 text-center">
              <p className="mb-3 text-sm text-zinc-500">Try one of these:</p>
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((e) => (
                  <button key={e} onClick={() => ask(e)} className="rounded-full border border-zinc-300 px-3 py-1.5 text-sm hover:bg-zinc-50">{e}</button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className="space-y-3">
              <div className="flex justify-end gap-2">
                <div className="max-w-[80%] rounded-lg rounded-tr-none bg-zinc-900 px-3 py-2 text-sm text-white">{t.q}</div>
                <div className="grid size-7 shrink-0 place-items-center rounded-full bg-zinc-200"><UserIcon className="size-4" /></div>
              </div>
              <div className="flex gap-2">
                <div className="grid size-7 shrink-0 place-items-center rounded-full bg-amber-400"><Bot className="size-4 text-zinc-950" /></div>
                <div className="max-w-[85%] flex-1 rounded-lg rounded-tl-none border border-zinc-200 px-3 py-2">
                  {t.error && <div className="text-sm text-red-700">{t.error}</div>}
                  {!t.r && !t.error && <div className="text-sm text-zinc-500">Searching memory…</div>}
                  {t.r && (
                    <>
                      <div className="mb-1.5 flex gap-1.5">
                        <Badge tone={t.r.generated_by !== "template" ? "warn" : "neutral"}>{t.r.generated_by !== "template" ? llmLabel(t.r.generated_by) : "No LLM: memory listing"}</Badge>
                        {!t.r.sufficient && <Badge tone="violet">memory insufficient</Badge>}
                      </div>
                      <AnswerText text={t.r.answer} memories={t.r.memories} />
                      <Memories r={t.r} />
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <form onSubmit={(e) => { e.preventDefault(); ask(q); }} className="flex flex-wrap gap-2 border-t border-zinc-200 p-3">
          <Select value={assetTag} onChange={(e) => setAssetTag(e.target.value)} className="w-full sm:w-44">
            <option value="">All machines</option>
            {assets.data?.map((a) => <option key={a.id} value={a.asset_tag}>{a.asset_tag}</option>)}
          </Select>
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about a machine's history…" className="min-w-0 flex-1" />
          <Button type="submit" disabled={busy || !q.trim()}><Send /> Ask</Button>
        </form>
      </div>
    </>
  );
}
