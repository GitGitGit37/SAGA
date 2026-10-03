"""Proposers turn an Analysis into candidate interpretations.

ClaudeProposer asks Claude to interpret the evidence. RuleProposer builds the same
shape from the engine's best hypothesis; it is used when no API key is configured
and as the engine's own alternative when it disagrees with Claude.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from app.db.enums import Hypothesis
from app.evidence.engine import Analysis, Signal
from app.evidence.maintenance import SUBSYSTEM_METRICS
from app.evidence.types import Finding
from app.inference.schemas import Claim, Proposal, ProposalSet
from app.llm.client import StructuredLLM

H = Hypothesis

SUBSYSTEM_CATEGORY = {
    "cooling_system": "engine_health", "engine": "engine_health", "hydraulics": "hydraulics",
    "operator": "operator_behavior", "aftertreatment": "emissions", "electrical": "electrical",
    "environment": "environmental_condition",
}
SUBSYSTEM_METRICS_ALL = {**SUBSYSTEM_METRICS, "operator": ["idle_pct", "load_factor_pct", "fuel_rate_lph"],
                         "engine": ["oil_pressure_kpa", "engine_rpm_avg", "fuel_rate_lph"]}


class Proposer(Protocol):
    name: str

    def propose(self, analysis: Analysis) -> list[Proposal]: ...


# --------------------------------------------------------------------------- #
# Rule-based
# --------------------------------------------------------------------------- #

TITLES = {
    H.degradation: "Progressive {sub} degradation",
    H.acute_failure: "Acute {sub} failure risk",
    H.sensor_fault: "Suspected {sub} sensor fault",
    H.operating_practice: "Operating practice issue: {sub}",
    H.environmental: "{sub} stress from site conditions",
    H.resolved: "{sub} issue resolved by repair",
}
ACTIONS = {
    H.degradation: "Schedule an inspection of the {sub} before the next shift block.",
    H.acute_failure: "Inspect the {sub} before further operation.",
    H.sensor_fault: "Check the sensor and its connector/wiring before replacing components.",
    H.operating_practice: "Review operating practice with the operator; set idle-shutdown if available.",
    H.environmental: "Adjust duty cycle for site conditions and keep monitoring.",
    H.resolved: "Keep monitoring for a week to confirm the repair holds.",
}


def finding_to_claim(f: Finding) -> Claim:
    prefix = f.key.split(":")[0]
    ctype = {"threshold": "threshold_exceeded", "trend": "trend", "fault": "fault_code", "note": "note",
             "peers": "peer_comparison", "ambient": "ambient", "isolated": "isolated_spike",
             "repair": "repair", "iforest": "trend"}.get(prefix, "trend")
    code = f.key.split(":")[1] if prefix == "fault" else None
    return Claim(type=ctype, statement=f.description, metric=f.metric if prefix != "ambient" else None,
                 code=code, observation_ids=f.observation_ids[:10])


def rule_proposal(signal: Signal, hypothesis: str | None = None) -> Proposal:
    hs = signal.score(hypothesis) if hypothesis else signal.best()
    sub = signal.subsystem.replace("_", " ")
    top = sorted(hs.supporting, key=lambda f: f.strength * f.effect_on(hs.hypothesis), reverse=True)[:4]
    against = sorted(hs.contradicting, key=lambda f: f.strength, reverse=True)[:2]
    body = " ".join(f.description for f in top)
    if against:
        body += " Counter-evidence: " + " ".join(f.description for f in against)
    category = "safety" if signal.safety and hs.hypothesis not in (H.resolved, H.sensor_fault) \
        else SUBSYSTEM_CATEGORY.get(signal.subsystem, "engine_health")
    return Proposal(
        subsystem=signal.subsystem,
        category=category,
        hypothesis=hs.hypothesis,
        title=TITLES[hs.hypothesis].format(sub=sub).capitalize(),
        interpretation=body or f"No strong evidence for {hs.hypothesis} in the {sub}.",
        recommended_action=ACTIONS[hs.hypothesis].format(sub=sub),
        confidence=round(hs.score, 3),
        claims=[finding_to_claim(f) for f in top],
    )


class RuleProposer:
    name = "rules"

    def propose(self, analysis: Analysis) -> list[Proposal]:
        return [rule_proposal(s) for s in analysis.signals]


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are the interpretation layer of Ride Along, a memory system for heavy \
equipment (Caterpillar excavators, wheel loaders, dozers). A deterministic evidence engine has \
already analysed one machine's telematics, fault codes and maintenance notes and found SIGNALS: \
subsystems where something is going on. Your job is to say what each signal most likely means.

For each subsystem under SIGNALS, return exactly one proposal:
- hypothesis: degradation (gradual wear, blockage, leak), acute_failure (sudden failure), \
sensor_fault (the reading is wrong, not the machine), operating_practice (how it is being run), \
environmental (site conditions explain it), or resolved (a recorded repair fixed it).
- category: the category that best fits. Use "safety" when the issue puts people at risk \
(e.g. steering, brakes, loss of implement control).
- claims: the specific facts you rely on, each citing observation ids from the data below.

Every claim is machine-checked against the raw data. Claims that cite ids that don't exist, \
or state things the data doesn't show, are discarded and lower your score. Only claim what you \
can see in the data provided. Weigh counter-evidence (e.g. normal ambient temperature, normal \
peers, isolated spikes, post-repair readings) explicitly. Give a calibrated confidence: what \
fraction of machines with exactly this evidence would turn out to have this explanation."""


def _fmt_time(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def build_prompt(analysis: Analysis) -> str:
    ctx = analysis.ctx
    lines = [
        f"MACHINE: {ctx.asset_tag} ({ctx.asset_type}) at {ctx.site}",
        f"ANALYSIS WINDOW: {_fmt_time(ctx.window_start)} to {_fmt_time(ctx.as_of)}",
        "",
        "SIGNALS (engine findings, grouped by subsystem):",
    ]
    for s in analysis.signals:
        lines.append(f"\n## {s.subsystem}{'  [engine flagged SAFETY]' if s.safety else ''}")
        for f in s.findings:
            lines.append(f"- [{f.key}] {f.description}")

    window = ctx.window_telematics
    lines.append("\nTELEMATICS IN WINDOW (id | time | values):")
    metrics = sorted({m for s in analysis.signals for m in SUBSYSTEM_METRICS_ALL.get(s.subsystem, [])})
    metrics = ["ambient_temp_c", *[m for m in metrics if m in window]]
    lines.append("id | time | " + " | ".join(metrics))
    for _, r in window.iterrows():
        lines.append(f"{int(r.id)} | {_fmt_time(r.observed_at)} | " + " | ".join(f"{r[m]:g}" for m in metrics))

    ref = ctx.reference_telematics()
    if len(ref):
        lines.append("\nBASELINE (this machine, reference period): " + "; ".join(
            f"{m} {ref[m].mean():.1f} ± {ref[m].std():.1f}" for m in metrics))

    f = ctx.faults
    wf = f[(f.observed_at >= ctx.window_start) & (f.observed_at <= ctx.as_of)]
    lines.append("\nFAULT CODES IN WINDOW (id | time | code | occurrences):")
    lines += [f"{int(r.id)} | {_fmt_time(r.observed_at)} | {r.code} | {r.occurrences}" for _, r in wf.iterrows()] or ["none"]

    n = ctx.notes
    recent = n[(n.observed_at >= ctx.window_start - pd.Timedelta(days=21)) & (n.observed_at <= ctx.as_of)]
    lines.append("\nNOTES (id | time | kind | author | text):")
    lines += [f"{int(r.id)} | {_fmt_time(r.observed_at)} | {r.kind} | {r.author_role} | {r.text}"
              for _, r in recent.iterrows()] or ["none"]
    return "\n".join(lines)


class ClaudeProposer:
    def __init__(self, llm: StructuredLLM):
        self.llm = llm
        self.name = getattr(llm, "name", "claude")

    def propose(self, analysis: Analysis) -> list[Proposal]:
        if not analysis.signals:
            return []
        result = self.llm.parse(tier="fast", system=SYSTEM_PROMPT, prompt=build_prompt(analysis),
                                schema=ProposalSet, effort="medium")
        return result.proposals
