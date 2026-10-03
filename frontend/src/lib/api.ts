// Typed client for the Cat Track API (/api, proxied by Vite to the backend).

export type User = { id: number; name: string; role: string; reliability: number };

export type Confidence = {
  evidence_score: number;
  llm_confidence_raw: number;
  llm_confidence_verified: number;
  base_rate: number;
  final_confidence: number;
  weights: { evidence: number; llm: number; history: number };
};

export type InferenceBrief = {
  id: number;
  lineage_id: string;
  version: number;
  asset_id: number;
  category: string;
  subsystem: string;
  hypothesis: string;
  title: string;
  status: "active" | "contested" | "superseded" | "confirmed";
  is_safety_critical: boolean;
  escalated: boolean;
  final_confidence: number;
  created_at: string;
  window_end: string | null;
  created_by: string;
};

export type Inference = InferenceBrief & {
  interpretation: string;
  recommended_action: string | null;
  window_start: string | null;
  supersedes_id: number | null;
  change_reason: string | null;
  confidence: Confidence;
};

export type Asset = {
  id: number;
  asset_tag: string;
  name: string;
  asset_type: string;
  model: string;
  serial_number: string;
  site: string;
};

export type Alert = { code: string; observed_at: string; description: string; severity: number | null };

export type AssetSummary = Asset & {
  health: "ok" | "warning" | "critical";
  counts: { active: number; contested: number; confirmed: number; escalated: number };
  inferences: InferenceBrief[];
  recent_alerts: Alert[];
  last_observation: string | null;
};

export type Observation = {
  id: number;
  asset_id: number;
  asset_tag?: string;
  observed_at: string;
  kind: string;
  source: string;
  payload: Record<string, unknown>;
  text: string | null;
  category: string | null;
  category_confidence: number | null;
};

export type Feedback = {
  id: number;
  inference_id: number;
  user: User;
  feedback_type: string;
  rationale: string | null;
  proposed_hypothesis: string | null;
  proposed_category: string | null;
  proposed_interpretation: string | null;
  attached_observation_ids: number[];
  role_weight: number | null;
  reliability_at_submission: number | null;
  evidence_against: number | null;
  weight: number | null;
  scores: Record<string, unknown>;
  outcome: string;
  system_response: string | null;
  resulting_inference_id: number | null;
  data_verdict: string | null;
  created_at: string;
};

export type TelemetryRow = { id: number; t: string } & Record<string, number | string | null>;

export type TimelineEvent =
  | { type: "inference"; at: string; inference: InferenceBrief }
  | { type: "feedback"; at: string; feedback: Feedback }
  | { type: string; at: string; observation: Observation };

export type AssetDetail = Asset & {
  live_inferences: Inference[];
  telematics: TelemetryRow[];
  limits: Record<string, { warn: number; critical: number }>;
  confidence_trend: {
    lineage_id: string;
    subsystem: string;
    version: number;
    hypothesis: string;
    final_confidence: number;
    at: string;
    inference_id: number;
  }[];
  events: TimelineEvent[];
};

export type Evidence = {
  id: number;
  label: string;
  kind: string;
  direction: "supports" | "contradicts";
  description: string;
  strength: number;
  verified: boolean;
  source_type: string;
  source_ref: string;
  observation_id: number | null;
  details: Record<string, unknown>;
  observation: Observation | null;
};

export type Alternative = {
  hypothesis: string;
  category: string;
  title: string;
  interpretation: string;
  proposer: string;
  confidence: Confidence;
  reason: string;
};

export type InferenceDetail = Inference & {
  asset: Asset;
  evidence: Evidence[];
  versions: (InferenceBrief & { change_reason: string | null })[];
  latest_id: number | null;
  feedback: Feedback[];
  alternatives: Alternative[];
  audit: { ts: string; actor: string; action: string; entity_id: number }[];
};

export type Meta = {
  llm_enabled: boolean;
  llm_provider: string | null;
  hypotheses: string[];
  categories: string[];
  feedback_types: string[];
  thresholds: { metrics: Record<string, { label: string; unit: string; subsystem: string; direction: string }> };
  scoring: { feedback: { revision_margin: number; role_weights: Record<string, number> } };
};

export type FeedbackPayload = {
  user_id: number;
  feedback_type: string;
  rationale?: string;
  proposed_hypothesis?: string;
  proposed_category?: string;
  proposed_interpretation?: string;
  new_record?: { text: string; observed_at: string; kind?: string };
};

export type FeedbackResult = {
  feedback: Feedback;
  inference: InferenceBrief;
  decision: { action: string; combined: Record<string, number>; reasons: string[] } | null;
  reply: { message: string; cited_evidence: string[]; data_requests: string[] };
};

export type IngestResult = {
  ingested: number;
  by_category: Record<string, number>;
  observations: Observation[];
  inferences_created: (InferenceBrief & { asset_tag: string })[];
};

export type Memory = {
  ref: string;
  owner_type: "inference" | "observation";
  owner_id: number;
  asset_tag: string;
  content: string;
  similarity: number;
  score: number;
  when: string | null;
  meta: Record<string, unknown>;
};

export type RecallResult = {
  answer: string;
  cited: string[];
  sufficient: boolean;
  generated_by: "claude" | "gemini" | "template";
  filters: { asset_tags: string[]; subsystem: string | null; status: string | null; safety_only: boolean };
  memories: Memory[];
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  meta: () => request<Meta>("/meta"),
  users: () => request<User[]>("/users"),
  assets: () => request<AssetSummary[]>("/assets"),
  asset: (tag: string) => request<AssetDetail>(`/assets/${encodeURIComponent(tag)}`),
  analyze: (tag: string) =>
    request<{ created: InferenceBrief[] }>(`/assets/${encodeURIComponent(tag)}/analyze`, { method: "POST" }),
  inference: (id: number) => request<InferenceDetail>(`/inferences/${id}`),
  feedback: (id: number, body: FeedbackPayload) => request<FeedbackResult>(`/inferences/${id}/feedback`, json(body)),
  ingest: (form: FormData) => request<IngestResult>("/ingest", { method: "POST", body: form }),
  recall: (body: { question: string; asset_tag?: string }) => request<RecallResult>("/recall", json(body)),
};
