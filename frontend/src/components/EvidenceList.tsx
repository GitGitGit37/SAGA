import { Check, ChevronDown, X } from "lucide-react";
import { useState } from "react";
import { Meter } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import type { Evidence } from "@/lib/api";
import { fmtDateTime, humanize } from "@/lib/utils";

function Source({ e }: { e: Evidence }) {
  const [open, setOpen] = useState(false);
  const o = e.observation;
  const ids = (e.details.observation_ids as number[] | undefined) ?? [];
  return (
    <div className="mt-1.5 text-[11px] text-zinc-500">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-0.5 hover:text-zinc-800">
        <ChevronDown className={`size-3 transition-transform ${open ? "" : "-rotate-90"}`} />
        source: {e.source_ref}
        {ids.length > 0 && <> · {ids.length} record{ids.length > 1 ? "s" : ""}</>}
      </button>
      {open && (
        <div className="mt-1 rounded border border-zinc-200 bg-zinc-50 p-2 font-mono text-[11px] text-zinc-700">
          {o ? (
            <>
              <div>
                #{o.id} · {o.kind} · {fmtDateTime(o.observed_at)} · {o.source}
              </div>
              {o.text ? (
                <div className="mt-1 whitespace-pre-wrap font-sans">{o.text}</div>
              ) : (
                <div className="mt-1 break-all">{JSON.stringify(o.payload)}</div>
              )}
            </>
          ) : (
            <div>{e.source_type} reference</div>
          )}
          {ids.length > 1 && <div className="mt-1 text-zinc-500">records: {ids.slice(0, 20).map((i) => `#${i}`).join(" ")}{ids.length > 20 ? " …" : ""}</div>}
        </div>
      )}
    </div>
  );
}

export function EvidenceItem({ e }: { e: Evidence }) {
  const supports = e.direction === "supports";
  return (
    <li className="rounded-md border border-zinc-200 p-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-zinc-400">{e.label}</span>
        <Badge tone={supports ? "neutral" : "violet"}>{humanize(e.kind)}</Badge>
        {Boolean(e.details.safety) && <Badge tone="danger">safety</Badge>}
        <div className="ml-auto flex w-24 items-center gap-2">
          <Meter value={e.strength} tone={supports ? "zinc" : "red"} />
          <span className="tabular w-8 text-right text-[11px] text-zinc-500">{e.strength.toFixed(2)}</span>
        </div>
      </div>
      <p className="mt-1.5 text-sm leading-snug">{e.description}</p>
      <Source e={e} />
    </li>
  );
}

export function ClaimItem({ e }: { e: Evidence }) {
  return (
    <li className="flex gap-2 text-sm">
      {e.verified ? (
        <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
      ) : (
        <X className="mt-0.5 size-4 shrink-0 text-red-600" />
      )}
      <div>
        <span className={e.verified ? "" : "text-zinc-500 line-through"}>{e.description}</span>
        <div className="text-[11px] text-zinc-500">
          {humanize(String(e.details.claim_type))} · {String(e.details.verification)}
        </div>
      </div>
    </li>
  );
}
