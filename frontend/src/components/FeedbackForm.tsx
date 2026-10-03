import { Send } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label, Select, Textarea } from "@/components/ui/form";
import { api, type FeedbackResult, type InferenceDetail } from "@/lib/api";
import { useSession } from "@/lib/session";
import { humanize, ROLE_LABEL } from "@/lib/utils";

const TYPES = [
  { value: "confirm", label: "Confirm", hint: "This matches what I see." },
  { value: "reject", label: "Reject", hint: "This is wrong. Say why." },
  { value: "correct", label: "Correct", hint: "It's something else. Propose what." },
  { value: "add_context", label: "Add context", hint: "Extra information, e.g. a repair record." },
];

function localNow() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export function FeedbackForm({ inf, onDone }: { inf: InferenceDetail; onDone: (r: FeedbackResult) => void }) {
  const { user, meta, refreshUsers } = useSession();
  const [type, setType] = useState("reject");
  const [rationale, setRationale] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [category, setCategory] = useState("");
  const [attach, setAttach] = useState(false);
  const [recordText, setRecordText] = useState("");
  const [recordAt, setRecordAt] = useState(localNow);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsRationale = type === "reject" || type === "correct";
  const canPropose = type === "reject" || type === "correct";

  const submit = async () => {
    if (!user) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.feedback(inf.id, {
        user_id: user.id,
        feedback_type: type,
        rationale: rationale || undefined,
        proposed_hypothesis: canPropose && hypothesis ? hypothesis : undefined,
        proposed_category: canPropose && category ? category : undefined,
        new_record: attach && recordText ? { text: recordText, observed_at: new Date(recordAt).toISOString() } : undefined,
      });
      setRationale("");
      setRecordText("");
      setAttach(false);
      refreshUsers();
      onDone(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="text-xs text-zinc-500">
        Giving feedback as <span className="font-medium text-zinc-800">{user?.name}</span> (
        {ROLE_LABEL[user?.role ?? ""] ?? user?.role}, reliability {user?.reliability.toFixed(2)}). Feedback is weighed as
        evidence. It does not override the data.
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {TYPES.map((t) => (
          <button key={t.value} onClick={() => setType(t.value)} title={t.hint}
            className={`rounded-md border px-3 py-2 text-left text-sm ${type === t.value ? "border-zinc-900 bg-zinc-900 text-white" : "border-zinc-300 bg-white hover:bg-zinc-50"}`}>
            <div className="font-medium">{t.label}</div>
            <div className={`text-[11px] ${type === t.value ? "text-zinc-300" : "text-zinc-500"}`}>{t.hint}</div>
          </button>
        ))}
      </div>

      {canPropose && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label>What do you think it is?</Label>
            <Select value={hypothesis} onChange={(e) => setHypothesis(e.target.value)}>
              <option value="">{type === "reject" ? "(no alternative)" : "Choose…"}</option>
              {meta?.hypotheses.filter((h) => h !== inf.hypothesis).map((h) => (
                <option key={h} value={h}>{humanize(h)}</option>
              ))}
            </Select>
          </div>
          {type === "correct" && (
            <div className="space-y-1">
              <Label>Category (optional)</Label>
              <Select value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">Keep {humanize(inf.category)}</option>
                {meta?.categories.filter((c) => c !== inf.category).map((c) => (
                  <option key={c} value={c}>{humanize(c)}</option>
                ))}
              </Select>
            </div>
          )}
        </div>
      )}

      <div className="space-y-1">
        <Label>Rationale{needsRationale && <span className="text-red-600"> *</span>}</Label>
        <Textarea value={rationale} onChange={(e) => setRationale(e.target.value)}
          placeholder={type === "confirm" ? "Optional" : "What you observed and why you think so"} />
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
        Attach a maintenance or inspection record (new, verifiable evidence)
      </label>
      {attach && (
        <div className="grid gap-3 rounded-md border border-zinc-200 bg-zinc-50 p-3 sm:grid-cols-[1fr_200px]">
          <div className="space-y-1">
            <Label>Record text</Label>
            <Textarea value={recordText} onChange={(e) => setRecordText(e.target.value)}
              placeholder="e.g. Replaced radiator core, WO-26-0912. Test run 45 min at load: max coolant 89 C." />
          </div>
          <div className="space-y-1">
            <Label>Work performed at</Label>
            <Input type="datetime-local" value={recordAt} onChange={(e) => setRecordAt(e.target.value)} />
          </div>
        </div>
      )}

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-2 text-sm text-red-700">{error}</div>}
      <Button onClick={submit} disabled={busy || !user || (needsRationale && !rationale.trim())}>
        <Send /> {busy ? "Weighing…" : "Submit feedback"}
      </Button>
    </div>
  );
}
