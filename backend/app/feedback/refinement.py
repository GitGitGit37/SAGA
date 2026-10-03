"""Feedback is evidence, not an override.

Pure decision logic, no DB. Given the current belief, the engine's view of the data,
and a feedback item, decide whether to revise, hold (contested), escalate, or confirm.

    weight = role_weight * user_reliability * (1 - evidence_against_feedback)

    model(h)    = blend of evidence_score(h) and base_rate(h)   (same formula for every h)
    human(h)    = sum of feedback weights endorsing h - sum of weights rejecting h
    combined(h) = model(h) + human(h)

Revise only if combined(alternative) > combined(current) + revision_margin.
Safety-critical beliefs are never revised or weakened by opinion: disagreement escalates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum

from app import config
from app.db.enums import FeedbackType, Hypothesis
from app.evidence.base_rates import BaseRate
from app.evidence.engine import Signal
from app.evidence.types import noisy_or

H = Hypothesis
PROBLEM = {H.degradation, H.acute_failure}


class Action(StrEnum):
    revise = "revise"          # new version with the alternative hypothesis
    hold = "hold"              # keep belief, mark contested, explain
    escalate = "escalate"      # safety-critical disagreement: keep belief, flag for humans
    confirm = "confirm"        # mark confirmed
    record = "record"          # logged as context, no change


class FeedbackRejected(ValueError):
    pass


@dataclass
class Belief:
    hypothesis: str
    category: str
    subsystem: str
    is_safety_critical: bool
    final_confidence: float


@dataclass
class FeedbackIn:
    feedback_type: str
    role: str
    reliability: float
    rationale: str | None = None
    proposed_hypothesis: str | None = None
    proposed_category: str | None = None
    has_new_evidence: bool = False


@dataclass
class PriorFeedback:
    """Feedback already on this lineage, as (hypothesis endorsed, hypothesis rejected, weight)."""
    endorses: str | None
    rejects: str | None
    weight: float


@dataclass
class Weight:
    role_weight: float
    reliability: float
    evidence_against: float
    weight: float

    def explain(self, role: str) -> str:
        return (f"{self.weight:.3f} = {role} weight {self.role_weight:g} × reliability {self.reliability:.2f}"
                f" × (1 − {self.evidence_against:.2f} evidence against it)")


@dataclass
class Decision:
    action: Action
    weight: Weight
    endorses: str | None
    rejects: str | None
    current_hypothesis: str
    alternative: str | None
    combined: dict[str, float]
    model: dict[str, float]
    margin: float
    reasons: list[str] = field(default_factory=list)

    @property
    def scores(self) -> dict:
        d = asdict(self)
        d["action"] = str(self.action)
        return d


def validate(fb: FeedbackIn) -> None:
    cfg = config.scoring()["feedback"]
    if fb.feedback_type not in set(FeedbackType):
        raise FeedbackRejected(f"unknown feedback type {fb.feedback_type!r}")
    if fb.feedback_type in cfg["rationale_required_for"] and not (fb.rationale or "").strip():
        raise FeedbackRejected(f"a rationale is required for {fb.feedback_type}")
    if fb.feedback_type == FeedbackType.correct and not (fb.proposed_hypothesis or fb.proposed_category):
        raise FeedbackRejected("a correction must propose a hypothesis or a category")
    if fb.proposed_hypothesis and fb.proposed_hypothesis not in set(Hypothesis):
        raise FeedbackRejected(f"unknown hypothesis {fb.proposed_hypothesis!r}")


def model_score(signal: Signal, hypothesis: str, base_rate: BaseRate) -> float:
    """Same formula for every hypothesis, so current and alternative compare fairly."""
    w = config.scoring()["confidence_weights"]
    rest = w["evidence"] + w["history"]
    return (w["evidence"] * signal.score(hypothesis).score + w["history"] * base_rate.value) / rest


def stance(fb: FeedbackIn, belief: Belief, signal: Signal) -> tuple[str | None, str | None]:
    """Which hypothesis the feedback endorses and which it rejects."""
    match fb.feedback_type:
        case FeedbackType.confirm:
            return belief.hypothesis, None
        case FeedbackType.reject:
            alternatives = [h for h in Hypothesis if h != belief.hypothesis]
            best_alt = max(alternatives, key=lambda h: signal.score(h).score)
            return (fb.proposed_hypothesis or best_alt), belief.hypothesis
        case FeedbackType.correct:
            if fb.proposed_hypothesis and fb.proposed_hypothesis != belief.hypothesis:
                return fb.proposed_hypothesis, belief.hypothesis
            return belief.hypothesis, None  # category-only correction
    return None, None


def evidence_against(signal: Signal, endorses: str | None, rejects: str | None) -> float:
    """How strongly the data contradicts what this feedback asserts."""
    parts = []
    if rejects is not None:
        parts.append(signal.score(rejects).score)  # evidence for the thing being rejected
    if endorses is not None:
        con = signal.score(endorses).contradicting
        parts.append(noisy_or([f.strength * -f.effect_on(endorses) for f in con]))
    return max(parts, default=0.0)


def feedback_weight(fb: FeedbackIn, against: float) -> Weight:
    role_w = config.scoring()["feedback"]["role_weights"][fb.role]
    w = role_w * fb.reliability * (1.0 - against)
    return Weight(role_w, fb.reliability, round(against, 4), round(w, 4))


def decide(belief: Belief, signal: Signal, fb: FeedbackIn, prior: list[PriorFeedback],
           base_rate_fn: Callable[[str, str], BaseRate]) -> Decision:
    validate(fb)
    cfg = config.scoring()
    margin = cfg["feedback"]["revision_margin"]
    safety_categories = set(cfg["safety"]["categories"])

    endorses, rejects = stance(fb, belief, signal)
    weight = feedback_weight(fb, evidence_against(signal, endorses, rejects))

    candidates = {belief.hypothesis} | {h for h in (endorses, rejects) if h}
    model = {h: round(model_score(signal, h, base_rate_fn(belief.category, h)), 4) for h in candidates}
    human = {h: 0.0 for h in candidates}
    for p in [*prior, PriorFeedback(endorses, rejects, weight.weight)]:
        if p.endorses in human:
            human[p.endorses] += p.weight
        if p.rejects in human:
            human[p.rejects] -= p.weight
    combined = {h: round(model[h] + human[h], 4) for h in candidates}

    alternative = endorses if endorses != belief.hypothesis else None
    reasons: list[str] = []

    def make(action: Action) -> Decision:
        return Decision(action, weight, endorses, rejects, belief.hypothesis, alternative,
                        combined, model, margin, reasons)

    # Category-only correction (same hypothesis).
    if fb.feedback_type == FeedbackType.correct and alternative is None:
        if fb.proposed_category in safety_categories and belief.category not in safety_categories:
            reasons.append("Raising an item to safety-critical is always accepted.")
            return make(Action.revise)
        reasons.append("Category follows from the evidence; correction recorded as context.")
        return make(Action.record)

    if fb.feedback_type == FeedbackType.add_context:
        reasons.append("Context recorded." + (" New records were re-evaluated." if fb.has_new_evidence else ""))
        return make(Action.record)

    if fb.feedback_type == FeedbackType.confirm:
        if combined[belief.hypothesis] >= cfg["feedback"]["confirm_threshold"]:
            reasons.append("Evidence and feedback agree.")
            return make(Action.confirm)
        reasons.append("Confirmation recorded, but the evidence isn't strong enough to mark it confirmed.")
        return make(Action.record)

    # reject / correct with a competing hypothesis
    gap = combined[alternative] - combined[belief.hypothesis]
    weakens_warning = belief.hypothesis in PROBLEM and alternative not in PROBLEM
    if belief.is_safety_critical and weakens_warning:
        reasons.append("Safety-critical warnings are never withdrawn on the strength of feedback alone; "
                       "escalated for human review.")
        return make(Action.escalate)
    if gap > margin:
        reasons.append(f"'{alternative}' beats '{belief.hypothesis}' by {gap:.2f} (> margin {margin}).")
        return make(Action.revise)
    reasons.append(f"'{alternative}' scores {combined[alternative]:.2f} vs {combined[belief.hypothesis]:.2f} "
                   f"for '{belief.hypothesis}'; it would need to win by more than {margin}.")
    if belief.is_safety_critical:
        reasons.append("Contested safety-critical item: escalated for human review.")
        return make(Action.escalate)
    return make(Action.hold)
