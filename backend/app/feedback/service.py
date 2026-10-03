"""Submit feedback on an inference: weigh it, maybe re-run with new evidence, apply the decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.enums import (EvidenceDirection, EvidenceKind, FeedbackOutcome, InferenceStatus,
                          ObservationKind)
from app.db.models import Feedback, Inference, RawObservation, User
from app.evidence.base_rates import lookup_base_rate
from app.evidence.context import load_context
from app.evidence.engine import analyze
from app.feedback.reconcile import reconcile_feedback
from app.feedback.refinement import (Action, Belief, Decision, FeedbackIn, FeedbackRejected,
                                     PriorFeedback, decide, validate)
from app.feedback.responses import EvidenceRef, HoldReply, write_reply
from app.inference.pipeline import EvidenceDraft, score_proposal
from app.inference.proposer import rule_proposal
from app.inference.run import run_for_asset
from app.inference.store import current_inference, save_draft, snapshot
from app.llm.client import StructuredLLM

OUTCOME = {Action.revise: FeedbackOutcome.applied, Action.confirm: FeedbackOutcome.applied,
           Action.hold: FeedbackOutcome.held, Action.escalate: FeedbackOutcome.escalated,
           Action.record: FeedbackOutcome.recorded}


@dataclass
class NewRecord:
    """A maintenance/inspection record submitted together with feedback."""
    text: str
    observed_at: datetime
    kind: str = ObservationKind.maintenance_note
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeedbackRequest:
    inference_id: int
    user_id: int
    feedback_type: str
    rationale: str | None = None
    proposed_hypothesis: str | None = None
    proposed_category: str | None = None
    proposed_interpretation: str | None = None
    attached_observation_ids: list[int] = field(default_factory=list)
    new_record: NewRecord | None = None


@dataclass
class FeedbackResult:
    feedback: Feedback
    inference: Inference          # the live version after processing
    decision: Decision | None     # None when new evidence alone changed the belief
    reply: HoldReply


def prior_feedback(db: Session, lineage_id, exclude_id: int) -> list[PriorFeedback]:
    rows = db.scalars(select(Feedback).join(Inference, Inference.id == Feedback.inference_id)
                      .where(Inference.lineage_id == lineage_id, Feedback.id != exclude_id,
                             Feedback.weight.is_not(None))).all()
    return [PriorFeedback(f.scores.get("endorses"), f.scores.get("rejects"), f.weight) for f in rows]


def evidence_refs(inf: Inference) -> list[EvidenceRef]:
    items = [e for e in inf.evidence if e.kind not in (EvidenceKind.llm_claim, EvidenceKind.base_rate)]
    items.sort(key=lambda e: e.strength, reverse=True)
    return [EvidenceRef(label=f"E{e.id}", description=e.description, direction=e.direction,
                        strength=e.strength) for e in items]


def _attach(db: Session, inf: Inference, user: User, req: FeedbackRequest) -> list[int]:
    ids = list(req.attached_observation_ids)
    if ids:
        found = db.scalars(select(RawObservation.id).where(RawObservation.id.in_(ids),
                                                           RawObservation.asset_id == inf.asset_id)).all()
        missing = set(ids) - set(found)
        if missing:
            raise FeedbackRejected(f"attached observations {sorted(missing)} don't belong to this asset")
    if req.new_record is not None:
        obs = RawObservation(asset_id=inf.asset_id, observed_at=req.new_record.observed_at,
                             kind=req.new_record.kind, source=f"feedback:user:{user.id}",
                             payload={**req.new_record.payload, "author_role": user.role},
                             text=req.new_record.text)
        db.add(obs)
        db.flush()
        audit.record(db, actor=f"user:{user.id}", action="observation.created_from_feedback",
                     entity_type="observation", entity_id=obs.id, after={"text": obs.text})
        ids.append(obs.id)
    return ids


def submit_feedback(db: Session, req: FeedbackRequest, llm: StructuredLLM | None) -> FeedbackResult:
    """Caller owns the transaction. Raises FeedbackRejected for invalid input."""
    inf = db.get(Inference, req.inference_id)
    user = db.get(User, req.user_id)
    if inf is None or user is None:
        raise FeedbackRejected("unknown inference or user")
    if inf.status == InferenceStatus.superseded:
        latest = current_inference(db, inf.asset_id, inf.subsystem)
        raise FeedbackRejected(f"inference #{inf.id} has been superseded"
                               + (f" by #{latest.id} (v{latest.version})" if latest else ""))

    fb_in = FeedbackIn(feedback_type=req.feedback_type, role=user.role, reliability=user.reliability,
                       rationale=req.rationale, proposed_hypothesis=req.proposed_hypothesis,
                       proposed_category=req.proposed_category)
    validate(fb_in)
    asset = inf.asset
    actor = f"user:{user.id}"

    attached = _attach(db, inf, user, req)
    fb_in.has_new_evidence = bool(attached)
    fb = Feedback(inference_id=inf.id, user_id=user.id, feedback_type=req.feedback_type,
                  rationale=req.rationale, proposed_category=req.proposed_category,
                  proposed_hypothesis=req.proposed_hypothesis,
                  proposed_interpretation=req.proposed_interpretation,
                  attached_observation_ids=attached, outcome=FeedbackOutcome.pending)
    db.add(fb)
    db.flush()
    audit.record(db, actor=actor, action="feedback.submitted", entity_type="feedback", entity_id=fb.id,
                 after={"type": req.feedback_type, "rationale": req.rationale,
                        "proposed_hypothesis": req.proposed_hypothesis, "attached": attached},
                 inference_id=inf.id)

    # 1. New verifiable records: re-run the pipeline with them, not just the opinion.
    rerun_note = None
    if attached:
        run_for_asset(db, asset, llm, actor=actor, created_by="pipeline:feedback_evidence",
                      change_reason=f"re-evaluated with records attached to feedback #{fb.id}")
        latest = current_inference(db, asset.id, inf.subsystem) or inf
        if latest.id != inf.id and latest.hypothesis != inf.hypothesis:
            fb.outcome = FeedbackOutcome.applied
            fb.resulting_inference_id = latest.id
            fb.role_weight, fb.reliability_at_submission = None, user.reliability
            fb.data_as_of = inf.window_end
            fb.scores = {"endorses": req.proposed_hypothesis or latest.hypothesis,
                         "rejects": inf.hypothesis if req.feedback_type in ("reject", "correct") else None,
                         "changed_by": "new_evidence"}
            reply = HoldReply(
                message=(f"Thanks. I re-ran the analysis with the record you attached. The data now supports "
                         f"'{latest.hypothesis.replace('_', ' ')}' ({latest.final_confidence:.0%}), so this is "
                         f"now version {latest.version}: {latest.title}. The change comes from the record "
                         f"and the readings, not from the opinion alone."),
                cited_evidence=[], data_requests=[])
            fb.system_response = reply.message
            audit.record(db, actor="system", action="feedback.processed", entity_type="feedback",
                         entity_id=fb.id, after={"outcome": fb.outcome, "resulting_inference_id": latest.id},
                         changed_by="new_evidence")
            reconcile_feedback(db, asset)
            return FeedbackResult(fb, latest, None, reply)
        rerun_note = (f"I re-ran the analysis with the record(s) you attached; the data still supports "
                      f"'{latest.hypothesis.replace('_', ' ')}'.")
        inf = latest

    # 2. Weigh the feedback as evidence against the engine's view of the data.
    analysis = analyze(load_context(db, asset))
    signal = analysis.signal(inf.subsystem)

    def base_rate(category: str, hypothesis: str):
        return lookup_base_rate(db, category=category, hypothesis=hypothesis, asset_type=asset.asset_type)

    belief = Belief(inf.hypothesis, inf.category, inf.subsystem, inf.is_safety_critical, inf.final_confidence)
    decision = decide(belief, signal, fb_in, prior_feedback(db, inf.lineage_id, fb.id), base_rate)

    fb.role_weight = decision.weight.role_weight
    fb.reliability_at_submission = decision.weight.reliability
    fb.evidence_against = decision.weight.evidence_against
    fb.weight = decision.weight.weight
    fb.scores = decision.scores
    fb.data_as_of = inf.window_end
    fb.outcome = OUTCOME[decision.action]

    # 3. Apply.
    before = snapshot(inf)
    result_inf = inf
    if decision.action == Action.revise:
        hypothesis = decision.alternative or inf.hypothesis
        proposal = rule_proposal(signal, hypothesis)
        if req.proposed_interpretation:
            proposal.interpretation = f"{req.proposed_interpretation} (proposed by {user.role}, supported by evidence)"
        if decision.alternative is None and req.proposed_category:
            proposal.category = req.proposed_category
        draft, _ = score_proposal(proposal, analysis, base_rate, llm_used=False)
        if req.proposed_category and decision.alternative is None:
            draft.category = req.proposed_category
            draft.is_safety_critical = True
        draft.evidence.append(EvidenceDraft(
            kind=EvidenceKind.context, direction=EvidenceDirection.supports,
            description=f"Feedback #{fb.id} ({user.role}): {req.rationale or req.feedback_type}",
            strength=decision.weight.weight, source_type="feedback", source_ref=f"feedback:{fb.id}",
            details={"weight": decision.weight.weight, "combined": decision.combined}))
        result_inf = save_draft(db, asset, draft, created_by="refinement", actor=actor, force_new_version=True,
                                change_reason=f"feedback #{fb.id}: " + " ".join(decision.reasons))
        fb.resulting_inference_id = result_inf.id
    elif decision.action in (Action.hold, Action.escalate):
        inf.status = InferenceStatus.contested
        if decision.action == Action.escalate:
            inf.escalated = True
        audit.record(db, actor="system", action=f"inference.{decision.action}", entity_type="inference",
                     entity_id=inf.id, before=before, after=snapshot(inf), feedback_id=fb.id,
                     reasons=decision.reasons)
    elif decision.action == Action.confirm:
        inf.status = InferenceStatus.confirmed
        audit.record(db, actor="system", action="inference.confirmed", entity_type="inference",
                     entity_id=inf.id, before=before, after=snapshot(inf), feedback_id=fb.id)

    reply = write_reply(decision, fb_in, evidence_refs(inf), title=inf.title,
                        interpretation=inf.interpretation, llm=llm, rerun_note=rerun_note)
    fb.system_response = reply.message + (
        "\n\nWhat would settle it: " + "; ".join(reply.data_requests) if reply.data_requests else "")
    audit.record(db, actor="system", action="feedback.processed", entity_type="feedback", entity_id=fb.id,
                 after={"outcome": fb.outcome, "weight": fb.weight,
                        "resulting_inference_id": fb.resulting_inference_id},
                 decision=decision.scores)
    return FeedbackResult(fb, result_inf, decision, reply)
