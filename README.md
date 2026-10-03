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
| 2 | Evidence Engine + inference pipeline | next |
| 3 | Feedback & refinement | |
| 4 | Recall / "Ask the machine" | |
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

**Inference confidence**

```
final = 0.5 * evidence_score + 0.2 * llm_verified + 0.3 * base_rate
```

- `evidence_score` comes from the deterministic Evidence Engine: threshold rules, fault code
  severity, and trend/anomaly detection over the asset's history.
- `llm_verified` is Claude's stated confidence minus a penalty (0.1) for each claim it made
  that the data does not back. Unbacked claims are also dropped from the evidence list.
- `base_rate` is how often this kind of inference has been confirmed on similar assets
  (Beta prior, so a handful of outcomes can't swing it).

**Feedback weight**

```
weight = role_weight * user_reliability * (1 - evidence_strength_against_feedback)
```

Role weights: operator 0.6, technician 1.0, fleet manager 0.8. Reliability starts at 0.5 and
moves as later data confirms or refutes a user's past feedback. An inference is only revised
when the alternative beats the current score by **0.15**; otherwise it is marked
**contested** and the system explains why it is holding its position. Safety-critical
inferences are never suppressed by feedback: disagreements escalate instead.

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
