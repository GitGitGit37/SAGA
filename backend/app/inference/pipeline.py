"""Inference pipeline: raw data -> evidence -> proposals -> verification -> scored drafts.

    build_drafts(analysis, proposer, base_rate_fn)   pure, no DB (used by tests)
    run_for_asset(db, asset, llm)                    loads data, builds drafts, persists versions

The LLM never has the final say:
  * every claim it makes is verified against the data; unverified claims cost confidence
  * evidence_score comes from the deterministic engine, not from the LLM
  * if the engine's own best hypothesis beats the LLM's by the revision margin, the
    engine's interpretation is the one stored, and the LLM's is kept as an alternative
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app import config
from app.db.enums import EvidenceDirection, EvidenceKind
from app.evidence.base_rates import BaseRate
from app.evidence.engine import Analysis
from app.evidence.types import Finding
from app.inference.proposer import Proposer, RuleProposer, rule_proposal
from app.inference.schemas import Proposal
from app.inference.scorer import ConfidenceBreakdown, blend
from app.inference.verifier import VerifiedClaim, verify_claims
from app.llm.client import LLMError

log = logging.getLogger(__name__)

BaseRateFn = Callable[[str, str], BaseRate]  # (category, hypothesis) -> BaseRate


@dataclass
class EvidenceDraft:
    kind: str
    direction: str
    description: str
    strength: float
    source_type: str
    source_ref: str
    verified: bool = True
    observation_id: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class InferenceDraft:
    asset_tag: str
    subsystem: str
    category: str
    hypothesis: str
    title: str
    interpretation: str
    recommended_action: str
    is_safety_critical: bool
    window_start: Any
    window_end: Any
    confidence: ConfidenceBreakdown
    evidence: list[EvidenceDraft]
    proposer: str
    alternatives: list[dict[str, Any]] = field(default_factory=list)


def _finding_evidence(f: Finding, hypothesis: str) -> EvidenceDraft:
    effect = f.effect_on(hypothesis)
    return EvidenceDraft(
        kind=f.kind,
        direction=EvidenceDirection.supports if effect > 0 else EvidenceDirection.contradicts,
        description=f.description,
        strength=round(f.strength * abs(effect), 4),
        source_type=f.source_type,
        source_ref=f.source_ref or f.key,
        observation_id=f.observation_ids[0] if f.observation_ids else None,
        details={**f.details, "finding_key": f.key, "observation_ids": f.observation_ids,
                 "raw_strength": f.strength, "effect": effect, "safety": f.safety},
    )


def _claim_evidence(vc: VerifiedClaim) -> EvidenceDraft:
    c = vc.claim
    return EvidenceDraft(
        kind=EvidenceKind.llm_claim,
        direction=EvidenceDirection.supports,
        description=c.statement,
        strength=0.0,  # LLM claims never add to evidence_score; they only gate LLM confidence
        source_type="observation" if c.observation_ids else "inference",
        source_ref=vc.finding_key or f"claim:{c.type}",
        verified=vc.verified,
        observation_id=c.observation_ids[0] if c.observation_ids else None,
        details={"claim_type": c.type, "metric": c.metric, "code": c.code,
                 "observation_ids": c.observation_ids, "verification": vc.reason},
    )


def score_proposal(p: Proposal, analysis: Analysis, base_rate_fn: BaseRateFn,
                   *, llm_used: bool) -> tuple[InferenceDraft, list[VerifiedClaim]]:
    ctx = analysis.ctx
    signal = analysis.signal(p.subsystem)
    hs = signal.score(p.hypothesis)
    claims = verify_claims(p.claims, analysis)
    br = base_rate_fn(p.category, p.hypothesis)
    breakdown = blend(hs.score, p.confidence, claims, br.value, llm_used=llm_used)

    safety_categories = set(config.scoring()["safety"]["categories"])
    evidence = [_finding_evidence(f, p.hypothesis) for f in hs.supporting + hs.contradicting]
    evidence += [_claim_evidence(vc) for vc in claims]
    evidence.append(EvidenceDraft(
        kind=EvidenceKind.base_rate, direction=EvidenceDirection.supports,
        description=br.description, strength=round(br.value, 4), source_type="inference",
        source_ref=f"base_rate:{p.category}:{p.hypothesis}:{ctx.asset_type}",
        details={"confirmed": br.confirmed, "refuted": br.refuted}))

    draft = InferenceDraft(
        asset_tag=ctx.asset_tag,
        subsystem=p.subsystem,
        category=p.category,
        hypothesis=p.hypothesis,
        title=p.title,
        interpretation=p.interpretation,
        recommended_action=p.recommended_action,
        # The engine's safety flag stands regardless of how the proposal is categorised.
        is_safety_critical=signal.safety or p.category in safety_categories,
        window_start=ctx.window_start,
        window_end=ctx.as_of,
        confidence=breakdown,
        evidence=evidence,
        proposer="claude" if llm_used else "rules",
    )
    return draft, claims


def build_drafts(analysis: Analysis, proposer: Proposer, base_rate_fn: BaseRateFn) -> list[InferenceDraft]:
    llm_used = not isinstance(proposer, RuleProposer)
    try:
        proposals = proposer.propose(analysis)
    except LLMError as exc:
        log.warning("proposer %s failed (%s); falling back to rules", proposer.name, exc)
        proposals, llm_used = RuleProposer().propose(analysis), False

    signal_subsystems = {s.subsystem for s in analysis.signals}
    margin = config.scoring()["feedback"]["revision_margin"]
    drafts: dict[str, InferenceDraft] = {}

    for p in proposals:
        if p.subsystem not in signal_subsystems:
            log.info("dropping proposal for %s: engine has no signal there", p.subsystem)
            continue
        draft, _ = score_proposal(p, analysis, base_rate_fn, llm_used=llm_used)

        # The engine gets a vote: if its best hypothesis beats the proposal's by the margin,
        # its interpretation wins and the LLM's is recorded as an alternative.
        signal = analysis.signal(p.subsystem)
        best = signal.best()
        if llm_used and best.hypothesis != p.hypothesis \
                and best.score - signal.score(p.hypothesis).score > margin:
            engine_draft, _ = score_proposal(rule_proposal(signal), analysis, base_rate_fn, llm_used=False)
            engine_draft.proposer = "engine_override"
            engine_draft.alternatives.append(_alternative(draft, "LLM proposal overruled by evidence"))
            draft = engine_draft

        previous = drafts.get(p.subsystem)
        if previous is None or draft.confidence.final_confidence > previous.confidence.final_confidence:
            if previous is not None:
                draft.alternatives.append(_alternative(previous, "lower confidence"))
            drafts[p.subsystem] = draft
        else:
            previous.alternatives.append(_alternative(draft, "lower confidence"))

    # Signals the proposer skipped still get an inference from the engine.
    for s in analysis.signals:
        if s.subsystem not in drafts:
            drafts[s.subsystem], _ = score_proposal(rule_proposal(s), analysis, base_rate_fn, llm_used=False)
    return list(drafts.values())


def _alternative(d: InferenceDraft, reason: str) -> dict[str, Any]:
    return {"hypothesis": d.hypothesis, "category": d.category, "title": d.title,
            "interpretation": d.interpretation, "proposer": d.proposer,
            "confidence": d.confidence.as_dict(), "reason": reason}
