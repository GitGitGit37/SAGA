import { FileUp, StickyNote } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ConfidencePill, HypothesisBadge, PageHeader, StatusBadge } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select, Textarea } from "@/components/ui/form";
import { api, type IngestResult } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { fmtDateTime, humanize, pct } from "@/lib/utils";

function preview(o: IngestResult["observations"][number]) {
  if (o.text) return o.text;
  if (o.kind === "fault_code") return `Fault ${String(o.payload.code)}`;
  const p = o.payload as Record<string, number>;
  return `coolant ${p.coolant_temp_max_c ?? "–"} °C · hyd ${p.hydraulic_pressure_bar ?? "–"} bar · idle ${p.idle_pct ?? "–"}%`;
}

export function Ingest() {
  const assets = useLoad(api.assets, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [assetTag, setAssetTag] = useState("");
  const [kind, setKind] = useState("inspection_note");
  const [analyze, setAnalyze] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(null);

  const send = async (mode: "file" | "text") => {
    const form = new FormData();
    if (mode === "file" && file) form.append("file", file);
    if (mode === "text") form.append("text", text);
    if (assetTag) form.append("asset_tag", assetTag);
    form.append("note_kind", kind);
    form.append("analyze", String(analyze));
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.ingest(form));
      if (mode === "text") setText("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Ingest data" subtitle="Upload telematics, fault codes or notes. Each record is categorised, then the machine's memory is re-evaluated." />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><FileUp className="size-4" /> Upload a file</CardTitle>
            <CardDescription>
              <b>.csv</b> telematics (asset_tag, timestamp, metrics) · <b>.json</b> fault codes or notes · <b>.txt</b> a single note.
              Demo files live in <code className="text-[11px]">backend/seed/data/demo_holdout/</code>.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => { e.preventDefault(); setFile(e.dataTransfer.files[0] ?? null); }}
              className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-zinc-300 bg-zinc-50 px-4 py-8 text-center text-sm text-zinc-500 hover:border-zinc-400"
            >
              <FileUp className="mb-2 size-6" />
              {file ? <span className="font-medium text-zinc-800">{file.name}</span> : "Drop a file here or click to choose"}
              <input ref={fileRef} type="file" accept=".csv,.json,.txt" className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </div>
            <Button onClick={() => send("file")} disabled={!file || busy} className="w-full">
              {busy ? "Ingesting…" : "Ingest file"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><StickyNote className="size-4" /> Write a note</CardTitle>
            <CardDescription>An operator or technician note, categorised on arrival.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Machine</Label>
                <Select value={assetTag} onChange={(e) => setAssetTag(e.target.value)}>
                  <option value="">Choose…</option>
                  {assets.data?.map((a) => <option key={a.id} value={a.asset_tag}>{a.asset_tag} · {a.name}</option>)}
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Kind</Label>
                <Select value={kind} onChange={(e) => setKind(e.target.value)}>
                  <option value="inspection_note">Inspection note</option>
                  <option value="maintenance_note">Maintenance note</option>
                </Select>
              </div>
            </div>
            <Textarea value={text} onChange={(e) => setText(e.target.value)} className="min-h-28"
              placeholder="e.g. Hydraulic hose on boom chafing against frame, small weep at fitting." />
            <Button onClick={() => send("text")} disabled={!text.trim() || !assetTag || busy} className="w-full">
              {busy ? "Ingesting…" : "Add note"}
            </Button>
          </CardContent>
        </Card>
      </div>

      <label className="mt-4 flex items-center gap-2 text-sm text-zinc-700">
        <input type="checkbox" checked={analyze} onChange={(e) => setAnalyze(e.target.checked)} />
        Re-analyse affected machines after ingest
      </label>

      {error && <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {result && (
        <div className="mt-6 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Ingested {result.ingested} record{result.ingested === 1 ? "" : "s"}</CardTitle>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {Object.entries(result.by_category).map(([c, n]) => <Badge key={c}>{humanize(c)} · {n}</Badge>)}
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-zinc-500">
                    <tr><th className="py-1 pr-3 font-normal">Machine</th><th className="py-1 pr-3 font-normal">When</th><th className="py-1 pr-3 font-normal">Kind</th><th className="py-1 pr-3 font-normal">Category</th><th className="py-1 font-normal">Content</th></tr>
                  </thead>
                  <tbody>
                    {result.observations.map((o) => (
                      <tr key={o.id} className="border-t border-zinc-100 align-top">
                        <td className="py-1.5 pr-3 font-mono text-xs">{o.asset_tag}</td>
                        <td className="py-1.5 pr-3 text-xs whitespace-nowrap">{fmtDateTime(o.observed_at)}</td>
                        <td className="py-1.5 pr-3 text-xs">{humanize(o.kind)}</td>
                        <td className="py-1.5 pr-3 text-xs whitespace-nowrap">
                          <span className="font-medium">{humanize(o.category)}</span>{" "}
                          <span className="text-zinc-400">{pct(o.category_confidence)}</span>
                        </td>
                        <td className="py-1.5 text-xs text-zinc-600">{preview(o)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Memory updates</CardTitle>
              <CardDescription>{result.inferences_created.length ? "New or revised beliefs from this data" : "No beliefs changed."}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {result.inferences_created.map((i) => (
                <Link key={i.id} to={`/inferences/${i.id}`} className="flex flex-wrap items-center gap-2 rounded-md border border-zinc-200 p-2.5 text-sm hover:bg-zinc-50">
                  <span className="font-mono text-xs">{i.asset_tag}</span>
                  <span className="font-medium">{i.title}</span>
                  <span className="text-xs text-zinc-500">v{i.version}</span>
                  <span className="ml-auto flex items-center gap-1.5"><StatusBadge inf={i} /><HypothesisBadge hypothesis={i.hypothesis} /><ConfidencePill value={i.final_confidence} /></span>
                </Link>
              ))}
            </CardContent>
          </Card>
        </div>
      )}
    </>
  );
}
