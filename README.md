# Cat Track (SAGA)

A persistent, self-improving memory layer for physical assets, built for Caterpillar's
"Memory for Physical AI" challenge. Every machine gets a memory: Cat Track ingests telematics,
fault codes and maintenance notes, infers what they mean, stores each inference as a versioned
memory with its evidence, answers natural-language questions from that memory, and takes
human feedback as **evidence, not an override**.

> Core principle: **the LLM proposes, the evidence engine decides.**

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | docker-compose, DB schema, seed generator | done |
| 2 | Evidence Engine + inference pipeline | done |
| 3 | Feedback & refinement | done |
| 4 | Recall / "Ask the machine" | next |
| 5 | Frontend | |
| 6 | Polish + demo script | |

## Prerequisites

- Python 3.11+
- Docker Desktop (for Postgres + pgvector)
- Node.js 20+ (frontend, phase 5)
- An Anthropic API key

## Setup

```bash
cp .env.example .env              # then set ANTHROPIC_API_KEY
docker compose up -d db           # Postgres 16 + pgvector on :5432

cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows (macOS/Linux: source .venv/bin/activate)
pip install -e ".[dev]"

alembic upgrade head              # create schema
python -m seed.generate           # write synthetic data to seed/data/
python -m seed.load --reset       # load it into Postgres
python -m app.inference.run       # categorise + infer for every asset (--rules to skip Claude)
uvicorn app.main:app --reload     # http://localhost:8000/health
pytest                            # tests (no DB or API key needed)
```

## Synthetic fleet

`python -m seed.generate` creates 10 assets across two sites with 60 days of shift-level
telematics (coolant temp, oil pressure, hydraulic pressure/temp, RPM, idle %, load, fuel),
fault codes, and maintenance/inspection notes. Ambient temperature is shared per site, so a
machine running hot on a normal day can be told apart from hot weather.

| Asset | Injected pattern |
|---|---|
| EX-320-A | Gradual radiator blockage: coolant drift + 110-xx faults while peers stay normal |
| EX-336-B | Hydraulic leak: pressure decay, oil top-ups in notes |
| DZ-D6-A | Fast hydraulic leak reaching a severity-4 fault, "steering heavy" note (safety) |
| WL-966-B | Overheating; the radiator replacement record and post-repair readings are in `seed/data/demo_holdout/` to upload live |
| WL-950-A | Chronic excessive idling, DPF soot faults |
| DZ-D8-B | Idling starts when a new operator is assigned |
| EX-336-D | Three isolated coolant sensor spikes, later traced to a corroded connector |
| EX-320-C, WL-950-C, DZ-D6-C | Healthy controls |

`seed/data/scenarios.json` records the ground truth for tests and the demo; it is never
loaded into the memory.

## Confidence math

All weights live in [`backend/config/scoring.yaml`](backend/config/scoring.yaml).

**Evidence score** (deterministic, [`app/evidence/`](backend/app/evidence/))

The engine turns raw data into findings: threshold breaches, fault codes (severity from the
lookup table), trends vs the asset's own baseline, isolated spikes, IsolationForest anomalies,
ambient temperature, same-site peer comparison, note symptoms and recorded repairs. Each
finding supports or contradicts each *hypothesis* (degradation, acute failure, sensor fault,
operating practice, environmental, resolved). For the hypothesis an inference asserts:

```
evidence_score = noisy_or(supporting strengths) * (1 - noisy_or(contradicting strengths))
```

capped at 0.98. That is why "it's just hot weather" scores low on EX-320-A: ambient isn't
elevated and the other machines on site are normal, both of which contradict *environmental*.

**Inference confidence**

```
final = 0.5 * evidence_score + 0.2 * llm_verified + 0.3 * base_rate
```

- `evidence_score` as above. Claude's claims never add to it.
- `llm_verified` is Claude's stated confidence minus a penalty (0.1) for each claim it made
  that the data does not back. Unbacked claims are also dropped from the evidence list.
- `base_rate` is how often this kind of inference has been confirmed on similar assets
  (Beta prior, so a handful of outcomes can't swing it).
- If the engine's best hypothesis beats Claude's by more than the revision margin (0.15), the
  engine's interpretation is stored and Claude's is kept as a recorded alternative.
- Without an API key the rule-based proposer is used; the LLM term is then dropped and its
  weight spread over the other two (0.625 evidence, 0.375 history).

**Feedback weight**

```
weight = role_weight * user_reliability * (1 - evidence_strength_against_feedback)
```

Role weights: operator 0.6, technician 1.0, fleet manager 0.8. Reliability starts at 0.5 and
moves 20% of the way toward 1 (or 0) each time later data confirms (or refutes) something the
user said. `evidence_against` is how strongly the data supports what the feedback rejects, or
contradicts what it proposes.

**Revision rule** ([`app/feedback/refinement.py`](backend/app/feedback/refinement.py))

```
model(h)    = 0.625 * evidence_score(h) + 0.375 * base_rate(h)     same formula for every h
combined(h) = model(h) + sum(weights endorsing h) - sum(weights rejecting h)
revise only if combined(alternative) > combined(current) + 0.15
```

Otherwise the inference is marked **contested** and the system replies explaining why it is
holding, citing evidence items, and naming the data that would settle it. On EX-320-A an
operator saying "it's just the heat" gets weight 0.6 × 0.5 × (1 − 0.98) ≈ 0.006, so even ten
such objections don't move it. On genuinely ambiguous data, a credible correction does win.

- **New verifiable evidence** (an attached or submitted maintenance record) re-runs the whole
  pipeline with that record. If the data then supports a different hypothesis, a new version
  is created because of the data, not the opinion.
- **Safety:** a safety-critical warning is never withdrawn or weakened by feedback, even if the
  math would allow it. The item is marked contested and **escalated** in the UI; people decide
  what to do with the machine. Raising an item to safety is always accepted.
- Every feedback item, weight, score breakdown, status change and reliability update is
  written to `audit_log`.

## Repository layout

```
docker-compose.yml        Postgres + pgvector
backend/
  config/                 thresholds, fault codes, categories, scoring weights (YAML)
  app/                    FastAPI app: db models, ingest, evidence, inference, feedback, recall
  alembic/                migrations
  seed/                   synthetic data generator + loader
  tests/
frontend/                 React + Vite (phase 5)
```

Fault codes are modeled on SAE J1939 SPN-FMI pairs and thresholds are illustrative; neither is
official Caterpillar data.
