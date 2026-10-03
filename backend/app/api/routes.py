"""JSON API used by the frontend. All routes live under /api."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import lru_cache

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.api import serializers as ser
from app.db.enums import FeedbackType, Hypothesis, InferenceStatus, ObservationKind
from app.db.models import Asset, AuditLog, EvidenceItem, Feedback, Inference, RawObservation, User
from app.db.session import get_db
from app.feedback.refinement import FeedbackRejected
from app.feedback.service import FeedbackRequest, NewRecord, submit_feedback
from app.inference.categorize import categorize_observations
from app.inference.run import run_for_asset
from app.inference.store import LIVE
from app.ingest.parsers import (ParseError, parse_fault_codes_json, parse_notes_json, parse_telematics_csv,
                                parse_text_note)
from app.ingest.store import store_observations
from app.llm.client import StructuredLLM, get_llm
from app.recall.answerer import answer_question
from app.recall.embeddings import get_embedder, sync_embeddings
from app.recall.retriever import Filters, infer_filters, retrieve

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
CHART_METRICS = ["coolant_temp_max_c", "hydraulic_pressure_bar", "hydraulic_oil_temp_c", "idle_pct",
                 "oil_pressure_kpa", "fuel_rate_lph", "ambient_temp_c"]


@lru_cache
def _llm() -> StructuredLLM | None:
    return get_llm()


def llm_dep() -> StructuredLLM | None:
    return _llm()


def _asset_or_404(db: Session, tag: str) -> Asset:
    a = db.scalar(select(Asset).where(Asset.asset_tag == tag))
    if a is None:
        raise HTTPException(404, f"no asset {tag}")
    return a


def _refresh_memory(db: Session) -> None:
    try:
        sync_embeddings(db, get_embedder())
    except Exception as exc:  # recall degrades; the write that triggered this must not fail
        log.warning("embedding sync failed: %s", exc)


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #

@router.get("/meta")
def meta(llm: StructuredLLM | None = Depends(llm_dep)) -> dict:
    return {"llm_enabled": llm is not None,
            "llm_provider": getattr(llm, "name", None),
            "hypotheses": [h.value for h in Hypothesis],
            "categories": list(config.categories().keys()),
            "feedback_types": [t.value for t in FeedbackType],
            "thresholds": config.thresholds(),
            "scoring": config.scoring()}


@router.get("/users")
def users(db: Session = Depends(get_db)) -> list[dict]:
    return [ser.user(u) for u in db.scalars(select(User).order_by(User.role, User.name))]


# --------------------------------------------------------------------------- #
# Fleet & assets
# --------------------------------------------------------------------------- #

def _health(live: list[Inference]) -> str:
    problems = [i for i in live if i.hypothesis in ("degradation", "acute_failure", "operating_practice",
                                                     "sensor_fault")]
    if any(i.is_safety_critical or i.escalated for i in problems):
        return "critical"
    if any(i.final_confidence >= 0.5 for i in problems):
        return "warning"
    return "ok"


@router.get("/assets")
def list_assets(db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for a in db.scalars(select(Asset).order_by(Asset.site, Asset.asset_tag)):
        live = db.scalars(select(Inference).where(Inference.asset_id == a.id, Inference.status.in_(LIVE))
                          .order_by(Inference.final_confidence.desc())).all()
        last = db.scalar(select(func.max(RawObservation.observed_at)).where(RawObservation.asset_id == a.id))
        alerts = []
        if last:
            faults = db.scalars(select(RawObservation)
                                .where(RawObservation.asset_id == a.id,
                                       RawObservation.kind == ObservationKind.fault_code,
                                       RawObservation.observed_at >= last - timedelta(days=7))
                                .order_by(RawObservation.observed_at.desc()).limit(3)).all()
            fc = config.fault_codes()
            alerts = [{"code": f.payload.get("code"), "observed_at": f.observed_at.isoformat(),
                       "description": fc.get(f.payload.get("code"), {}).get("description", "unknown code"),
                       "severity": fc.get(f.payload.get("code"), {}).get("severity")} for f in faults]
        out.append({**ser.asset(a), "health": _health(live),
                    "counts": {"active": sum(i.status == InferenceStatus.active for i in live),
                               "contested": sum(i.status == InferenceStatus.contested for i in live),
                               "confirmed": sum(i.status == InferenceStatus.confirmed for i in live),
                               "escalated": sum(i.escalated for i in live)},
                    "inferences": [ser.inference(i, brief=True) for i in live],
                    "recent_alerts": alerts,
                    "last_observation": last.isoformat() if last else None})
    return out


@router.get("/assets/{tag}")
def asset_detail(tag: str, db: Session = Depends(get_db)) -> dict:
    a = _asset_or_404(db, tag)
    obs = db.scalars(select(RawObservation).where(RawObservation.asset_id == a.id)
                     .order_by(RawObservation.observed_at)).all()
    infs = db.scalars(select(Inference).where(Inference.asset_id == a.id)
                      .order_by(Inference.window_end, Inference.version)).all()
    fbs = db.execute(select(Feedback, User).join(User, User.id == Feedback.user_id)
                     .join(Inference, Inference.id == Feedback.inference_id)
                     .where(Inference.asset_id == a.id).order_by(Feedback.created_at)).all()

    telematics = [{"id": o.id, "t": o.observed_at.isoformat(), **{m: o.payload.get(m) for m in CHART_METRICS}}
                  for o in obs if o.kind == ObservationKind.telematics]
    limits = config.thresholds()["asset_types"][a.asset_type]
    events = [{"type": o.kind, "at": o.observed_at.isoformat(), "observation": ser.observation(o)}
              for o in obs if o.kind != ObservationKind.telematics]
    events += [{"type": "inference", "at": i.created_at.isoformat(), "inference": ser.inference(i, brief=True)}
               for i in infs]
    events += [{"type": "feedback", "at": f.created_at.isoformat(), "feedback": ser.feedback(f, u)} for f, u in fbs]
    events.sort(key=lambda e: e["at"], reverse=True)

    return {**ser.asset(a),
            "live_inferences": [ser.inference(i) for i in infs if i.status in LIVE],
            "telematics": telematics,
            "limits": {m: limits[m] for m in limits},
            "confidence_trend": [{"lineage_id": str(i.lineage_id), "subsystem": i.subsystem, "version": i.version,
                                  "hypothesis": i.hypothesis, "final_confidence": i.final_confidence,
                                  "at": i.created_at.isoformat(), "inference_id": i.id} for i in infs],
            "events": events}


@router.post("/assets/{tag}/analyze")
def analyze_asset(tag: str, db: Session = Depends(get_db),
                  llm: StructuredLLM | None = Depends(llm_dep)) -> dict:
    a = _asset_or_404(db, tag)
    try:
        stored = run_for_asset(db, a, llm)
        _refresh_memory(db)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    return {"created": [ser.inference(i, brief=True) for i in stored]}


# --------------------------------------------------------------------------- #
# Inferences & feedback
# --------------------------------------------------------------------------- #

@router.get("/inferences/{inference_id}")
def inference_detail(inference_id: int, db: Session = Depends(get_db)) -> dict:
    inf = db.get(Inference, inference_id)
    if inf is None:
        raise HTTPException(404, "no such inference")
    items = db.scalars(select(EvidenceItem).where(EvidenceItem.inference_id == inf.id)).all()
    obs_ids = {e.observation_id for e in items if e.observation_id}
    obs = {o.id: o for o in db.scalars(select(RawObservation).where(RawObservation.id.in_(obs_ids)))} if obs_ids else {}
    versions = db.scalars(select(Inference).where(Inference.lineage_id == inf.lineage_id)
                          .order_by(Inference.version)).all()
    version_ids = [v.id for v in versions]
    fbs = db.execute(select(Feedback, User).join(User, User.id == Feedback.user_id)
                     .where(Feedback.inference_id.in_(version_ids)).order_by(Feedback.created_at)).all()
    audits = db.scalars(select(AuditLog).where(AuditLog.entity_type == "inference",
                                               AuditLog.entity_id.in_(version_ids))
                        .order_by(AuditLog.ts)).all()
    alternatives = next((a.details.get("alternatives", []) for a in audits
                         if a.action == "inference.created" and a.entity_id == inf.id), [])
    latest = next((v for v in reversed(versions) if v.status != InferenceStatus.superseded), None)
    return {**ser.inference(inf),
            "asset": ser.asset(inf.asset),
            "evidence": [ser.evidence(e, obs.get(e.observation_id)) for e in
                         sorted(items, key=lambda e: e.strength, reverse=True)],
            "versions": [ser.inference(v, brief=True) | {"change_reason": v.change_reason} for v in versions],
            "latest_id": latest.id if latest else None,
            "feedback": [ser.feedback(f, u) for f, u in fbs],
            "alternatives": alternatives,
            "audit": [{"ts": a.ts.isoformat(), "actor": a.actor, "action": a.action, "entity_id": a.entity_id,
                       "before": a.before, "after": a.after} for a in audits]}


class NewRecordIn(BaseModel):
    text: str
    observed_at: datetime
    kind: str = ObservationKind.maintenance_note


class FeedbackIn(BaseModel):
    user_id: int
    feedback_type: str
    rationale: str | None = None
    proposed_hypothesis: str | None = None
    proposed_category: str | None = None
    proposed_interpretation: str | None = None
    attached_observation_ids: list[int] = []
    new_record: NewRecordIn | None = None


@router.post("/inferences/{inference_id}/feedback")
def post_feedback(inference_id: int, body: FeedbackIn, db: Session = Depends(get_db),
                  llm: StructuredLLM | None = Depends(llm_dep)) -> dict:
    req = FeedbackRequest(
        inference_id=inference_id, user_id=body.user_id, feedback_type=body.feedback_type,
        rationale=body.rationale, proposed_hypothesis=body.proposed_hypothesis or None,
        proposed_category=body.proposed_category or None,
        proposed_interpretation=body.proposed_interpretation or None,
        attached_observation_ids=body.attached_observation_ids,
        new_record=NewRecord(body.new_record.text, body.new_record.observed_at, body.new_record.kind)
        if body.new_record else None)
    try:
        result = submit_feedback(db, req, llm)
        _refresh_memory(db)
        db.commit()
    except FeedbackRejected as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    u = db.get(User, body.user_id)
    return {"feedback": ser.feedback(result.feedback, u),
            "inference": ser.inference(result.inference, brief=True),
            "decision": result.decision.scores if result.decision else None,
            "reply": result.reply.model_dump()}


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #

@router.post("/ingest")
async def ingest(file: UploadFile | None = File(None), text: str | None = Form(None),
                 asset_tag: str | None = Form(None), observed_at: datetime | None = Form(None),
                 note_kind: str = Form(ObservationKind.inspection_note), analyze: bool = Form(True),
                 db: Session = Depends(get_db), llm: StructuredLLM | None = Depends(llm_dep)) -> dict:
    try:
        if file is not None:
            data = await file.read()
            name = (file.filename or "").lower()
            if name.endswith(".csv"):
                records, source = parse_telematics_csv(data), f"upload/{file.filename}"
            elif name.endswith(".json"):
                text_data = data.decode("utf-8")
                records = parse_fault_codes_json(text_data) if '"code"' in text_data else parse_notes_json(text_data)
                source = f"upload/{file.filename}"
            elif name.endswith(".txt"):
                if not asset_tag:
                    raise ParseError("asset_tag is required for a text note")
                records = parse_text_note(data.decode("utf-8"), asset_tag=asset_tag,
                                          observed_at=observed_at or datetime.now().astimezone(),
                                          kind=ObservationKind(note_kind))
                source = f"upload/{file.filename}"
            else:
                raise ParseError("supported files: .csv (telematics), .json (fault codes or notes), .txt (note)")
        elif text:
            if not asset_tag:
                raise ParseError("asset_tag is required for a text note")
            records = parse_text_note(text, asset_tag=asset_tag,
                                      observed_at=observed_at or datetime.now().astimezone(),
                                      kind=ObservationKind(note_kind))
            source = "upload/text"
        else:
            raise ParseError("send a file or text")
        rows = store_observations(db, records, source=source)
    except (ParseError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc))

    assets = {a.id: a for a in db.scalars(select(Asset).where(Asset.id.in_({r.asset_id for r in rows})))}
    created = []
    for a in assets.values():
        db.refresh(a, ["observations"])
        categorize_observations(db, a, llm)
        if analyze:
            created += run_for_asset(db, a, llm)
    _refresh_memory(db)
    db.commit()
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r.category or "uncategorised"] = by_cat.get(r.category or "uncategorised", 0) + 1
    return {"ingested": len(rows), "by_category": by_cat,
            "observations": [ser.observation(r) | {"asset_tag": assets[r.asset_id].asset_tag} for r in rows[:200]],
            "inferences_created": [ser.inference(i, brief=True) | {"asset_tag": assets[i.asset_id].asset_tag}
                                   for i in created]}


# --------------------------------------------------------------------------- #
# Recall
# --------------------------------------------------------------------------- #

class AskIn(BaseModel):
    question: str
    asset_tag: str | None = None
    subsystem: str | None = None
    status: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


@router.post("/recall")
def recall(body: AskIn, db: Session = Depends(get_db), llm: StructuredLLM | None = Depends(llm_dep)) -> dict:
    embedder = get_embedder()
    sync_embeddings(db, embedder)
    db.commit()
    tags = list(db.scalars(select(Asset.asset_tag)))
    explicit = Filters(asset_tags=[body.asset_tag] if body.asset_tag else [], subsystem=body.subsystem,
                       status=body.status, date_from=body.date_from, date_to=body.date_to)
    filters = infer_filters(body.question, tags, explicit)
    memories = retrieve(db, embedder, body.question, filters)
    answer = answer_question(body.question, memories, llm, filters)
    return {"answer": answer.text, "cited": answer.cited, "sufficient": answer.sufficient,
            "generated_by": answer.generated_by,
            "filters": {"asset_tags": filters.asset_tags, "subsystem": filters.subsystem, "status": filters.status,
                        "safety_only": filters.safety_only},
            "memories": [{"ref": m.ref, "owner_type": m.owner_type, "owner_id": m.owner_id,
                          "asset_tag": m.asset_tag, "content": m.content, "similarity": m.similarity,
                          "score": m.score, "when": m.when.isoformat() if m.when else None, "meta": m.meta}
                         for m in memories]}
