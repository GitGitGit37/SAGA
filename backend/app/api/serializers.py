"""Plain-dict views of ORM rows for the JSON API."""

from __future__ import annotations

from typing import Any

from app.db.models import Asset, EvidenceItem, Feedback, Inference, RawObservation, User


def user(u: User) -> dict[str, Any]:
    return {"id": u.id, "name": u.name, "role": u.role, "reliability": round(u.reliability, 3)}


def asset(a: Asset) -> dict[str, Any]:
    return {"id": a.id, "asset_tag": a.asset_tag, "name": a.name, "asset_type": a.asset_type,
            "model": a.model, "serial_number": a.serial_number, "site": a.site}


def observation(o: RawObservation) -> dict[str, Any]:
    return {"id": o.id, "asset_id": o.asset_id, "observed_at": o.observed_at.isoformat(), "kind": o.kind,
            "source": o.source, "payload": o.payload, "text": o.text, "category": o.category,
            "category_confidence": o.category_confidence}


def inference(i: Inference, *, brief: bool = False) -> dict[str, Any]:
    d = {"id": i.id, "lineage_id": str(i.lineage_id), "version": i.version, "asset_id": i.asset_id,
         "category": i.category, "subsystem": i.subsystem, "hypothesis": i.hypothesis, "title": i.title,
         "status": i.status, "is_safety_critical": i.is_safety_critical, "escalated": i.escalated,
         "final_confidence": i.final_confidence, "created_at": i.created_at.isoformat(),
         "window_end": i.window_end.isoformat() if i.window_end else None, "created_by": i.created_by}
    if brief:
        return d
    return {**d,
            "interpretation": i.interpretation, "recommended_action": i.recommended_action,
            "window_start": i.window_start.isoformat() if i.window_start else None,
            "supersedes_id": i.supersedes_id, "change_reason": i.change_reason,
            "confidence": {"evidence_score": i.evidence_score, "llm_confidence_raw": i.llm_confidence_raw,
                           "llm_confidence_verified": i.llm_confidence_verified, "base_rate": i.base_rate,
                           "final_confidence": i.final_confidence, "weights": i.weights}}


def evidence(e: EvidenceItem, obs: RawObservation | None = None) -> dict[str, Any]:
    return {"id": e.id, "label": f"E{e.id}", "kind": e.kind, "direction": e.direction,
            "description": e.description, "strength": e.strength, "verified": e.verified,
            "source_type": e.source_type, "source_ref": e.source_ref, "observation_id": e.observation_id,
            "details": e.details, "observation": observation(obs) if obs else None}


def feedback(f: Feedback, u: User | None = None) -> dict[str, Any]:
    return {"id": f.id, "inference_id": f.inference_id, "user": user(u) if u else {"id": f.user_id},
            "feedback_type": f.feedback_type, "rationale": f.rationale,
            "proposed_hypothesis": f.proposed_hypothesis, "proposed_category": f.proposed_category,
            "proposed_interpretation": f.proposed_interpretation,
            "attached_observation_ids": f.attached_observation_ids,
            "role_weight": f.role_weight, "reliability_at_submission": f.reliability_at_submission,
            "evidence_against": f.evidence_against, "weight": f.weight, "scores": f.scores,
            "outcome": f.outcome, "system_response": f.system_response,
            "resulting_inference_id": f.resulting_inference_id, "data_verdict": f.data_verdict,
            "created_at": f.created_at.isoformat()}
