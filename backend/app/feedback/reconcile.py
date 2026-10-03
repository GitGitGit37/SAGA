"""Judge past feedback against later data and update user reliability."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.db.models import Asset, Feedback, Inference, User
from app.feedback.reliability import judge, updated_reliability
from app.inference.store import LIVE


def reconcile_feedback(db: Session, asset: Asset) -> int:
    """For each live inference on this asset that was produced from data newer than a
    feedback item on the same lineage, record whether the data confirmed or refuted that
    feedback and move the user's reliability. Returns the number of verdicts recorded."""
    verdicts = 0
    live = db.scalars(select(Inference).where(Inference.asset_id == asset.id, Inference.status.in_(LIVE))).all()
    for inf in live:
        if not inf.created_by.startswith("pipeline"):
            continue  # a refinement version reflects feedback itself; only data may judge feedback
        pending = db.execute(
            select(Feedback, Inference.hypothesis)
            .join(Inference, Inference.id == Feedback.inference_id)
            .where(Inference.lineage_id == inf.lineage_id, Feedback.data_verdict.is_(None),
                   Feedback.data_as_of.is_not(None), Feedback.data_as_of < inf.window_end,
                   Feedback.created_at < inf.created_at)).all()
        for fb, original_hypothesis in pending:
            verdict = judge(endorses=fb.scores.get("endorses"), rejects=fb.scores.get("rejects"),
                            original_hypothesis=original_hypothesis, new_hypothesis=inf.hypothesis,
                            new_evidence_score=inf.evidence_score)
            if verdict is None:
                continue
            user = db.get(User, fb.user_id)
            before = user.reliability
            user.reliability = updated_reliability(before, verdict)
            fb.data_verdict = verdict
            verdicts += 1
            audit.record(db, actor="system", action="feedback.judged_by_data", entity_type="feedback",
                         entity_id=fb.id, before={"data_verdict": None}, after={"data_verdict": verdict},
                         judged_by_inference=inf.id, new_hypothesis=inf.hypothesis)
            audit.record(db, actor="system", action="user.reliability_updated", entity_type="user",
                         entity_id=user.id, before={"reliability": before},
                         after={"reliability": user.reliability}, feedback_id=fb.id, verdict=verdict)
    return verdicts
