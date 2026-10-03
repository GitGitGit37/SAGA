import type { Confidence } from "@/lib/api";
import { pct } from "@/lib/utils";

const PARTS = [
  { key: "evidence", label: "Evidence engine", color: "bg-zinc-900", value: (c: Confidence) => c.evidence_score,
    hint: "Deterministic: thresholds, faults, trends, peers, notes" },
  { key: "llm", label: "LLM (verified)", color: "bg-amber-500", value: (c: Confidence) => c.llm_confidence_verified,
    hint: "The LLM's confidence after unverified claims are removed" },
  { key: "history", label: "History (base rate)", color: "bg-sky-500", value: (c: Confidence) => c.base_rate,
    hint: "How often this kind of finding was confirmed on similar machines" },
] as const;

export function ConfidenceBreakdown({ c }: { c: Confidence }) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-zinc-500">Final confidence</span>
        <span className="tabular text-3xl font-semibold">{pct(c.final_confidence)}</span>
      </div>
      <div className="mt-2 flex h-3 w-full overflow-hidden rounded-full bg-zinc-200">
        {PARTS.map((p) => {
          const contribution = c.weights[p.key] * p.value(c);
          return (
            <div key={p.key} className={p.color} style={{ width: `${contribution * 100}%` }}
              title={`${p.label}: ${pct(contribution, 1)}`} />
          );
        })}
      </div>
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-zinc-500">
            <th className="pb-1 font-normal">Component</th>
            <th className="pb-1 text-right font-normal">Score</th>
            <th className="pb-1 text-right font-normal">Weight</th>
            <th className="pb-1 text-right font-normal">Adds</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {PARTS.map((p) => (
            <tr key={p.key} className="border-t border-zinc-100">
              <td className="py-1.5">
                <div className="flex items-center gap-2">
                  <span className={`size-2.5 rounded-sm ${p.color}`} />
                  <span>{p.label}</span>
                </div>
                <div className="pl-4.5 text-[11px] text-zinc-500">{p.hint}</div>
              </td>
              <td className="text-right">{pct(p.value(c))}</td>
              <td className="whitespace-nowrap text-right text-zinc-500">× {c.weights[p.key].toFixed(2)}</td>
              <td className="text-right font-medium">{pct(c.weights[p.key] * p.value(c), 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.weights.llm === 0 && (
        <p className="mt-2 text-[11px] text-zinc-500">
          Produced without an LLM, so the LLM term is dropped and its weight spread over the other two.
        </p>
      )}
      {c.llm_confidence_raw > c.llm_confidence_verified && c.weights.llm > 0 && (
        <p className="mt-2 text-[11px] text-amber-700">
          The LLM stated {pct(c.llm_confidence_raw)}; reduced to {pct(c.llm_confidence_verified)} because some of its claims
          weren't backed by the data.
        </p>
      )}
    </div>
  );
}
