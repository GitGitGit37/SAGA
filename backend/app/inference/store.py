"""Persist inference drafts as versioned memories.

One lineage per (asset, subsystem). A new run never updates an inference in place:
it inserts version N+1 and marks version N superseded, or does nothing if the
belief hasn't materially changed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.enums import InferenceStatus
from app.db.models import Asset, EvidenceItem, Inference
from app.inference.pipeline import InferenceDraft

UNCHANGED_TOLERANCE = 0.05
LIVE = [InferenceStatus.active, InferenceStatus.contested, InferenceStatus.confirmed]


def current_inference(db: Session, asset_id: int, subsystem: str) -> Inference | None:
    return db.scalars(select(Inference)
                      .where(Inference.asset_id == asset_id, Inference.subsystem == subsystem,
                             Inference.status.in_(LIVE))
                      .order_by(Inference.created_at.desc(), Inference.version.desc())).first()


def snapshot(inf: Inference) -> dict:
    return {"id": inf.id, "version": inf.version, "status": inf.status, "category": inf.category,
            "hypothesis": inf.hypothesis, "title": inf.title, "final_confidence": inf.final_confidence,
            "evidence_score": inf.evidence_score, "is_safety_critical": inf.is_safety_critical,
            "escalated": inf.escalated}


def save_draft(db: Session, asset: Asset, draft: InferenceDraft, *, created_by: str = "pipeline",
               change_reason: str | None = None, actor: str = "system",
               force_new_version: bool = False) -> Inference | None:
    """Store a draft. Returns the new version, or None if nothing changed."""
    current = current_inference(db, asset.id, draft.subsystem)
    before = snapshot(current) if current else None
    c = draft.confidence

    if current is not None and not force_new_version \
            and current.hypothesis == draft.hypothesis and current.category == draft.category \
            and abs(current.final_confidence - c.final_confidence) < UNCHANGED_TOLERANCE:
        return None

    status = InferenceStatus.active
    if current is not None and current.hypothesis == draft.hypothesis and \
            current.status in (InferenceStatus.contested, InferenceStatus.confirmed):
        status = current.status  # new data on the same belief keeps the human-review state

    inf = Inference(
        lineage_id=current.lineage_id if current else uuid.uuid4(),
        version=current.version + 1 if current else 1,
        supersedes_id=current.id if current else None,
        asset_id=asset.id,
        category=draft.category,
        subsystem=draft.subsystem,
        hypothesis=draft.hypothesis,
        title=draft.title[:255],
        interpretation=draft.interpretation,
        recommended_action=draft.recommended_action,
        status=status,
        is_safety_critical=draft.is_safety_critical,
        escalated=bool(current and current.escalated and draft.is_safety_critical),
        window_start=draft.window_start.to_pydatetime(),
        window_end=draft.window_end.to_pydatetime(),
        llm_confidence_raw=c.llm_confidence_raw,
        llm_confidence_verified=c.llm_confidence_verified,
        evidence_score=c.evidence_score,
        base_rate=c.base_rate,
        final_confidence=c.final_confidence,
        weights=c.weights,
        created_by=created_by if draft.proposer != "engine_override" else f"{created_by}:engine_override",
        change_reason=change_reason or ("new data" if current else "first inference"),
    )
    inf.evidence = [
        EvidenceItem(kind=e.kind, direction=e.direction, description=e.description, strength=e.strength,
                     verified=e.verified, source_type=e.source_type, source_ref=e.source_ref[:255],
                     observation_id=e.observation_id, details=e.details)
        for e in draft.evidence
    ]
    db.add(inf)
    db.flush()

    if current is not None:
        current.status = InferenceStatus.superseded
        audit.record(db, actor=actor, action="inference.superseded", entity_type="inference",
                     entity_id=current.id, before=before, after=snapshot(current), superseded_by=inf.id)
    audit.record(db, actor=actor, action="inference.created", entity_type="inference", entity_id=inf.id,
                 before=before, after=snapshot(inf),
                 proposer=draft.proposer, confidence=c.as_dict(), alternatives=draft.alternatives,
                 reason=inf.change_reason)
    return inf
