"""Hybrid retrieval: vector similarity plus structured filters.

Filters (asset, subsystem, status, date range) can be passed explicitly or are inferred
from the question ("excavator 320-A's hydraulics" -> asset EX-320-A, subsystem hydraulics).
Asset and date filters restrict the search; subsystem and status re-rank or restrict it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset, Embedding, Inference, RawObservation
from app.evidence.maintenance import SAFETY_TERMS
from app.inference.store import LIVE
from app.recall.embeddings import Embedder

SUBSYSTEM_WORDS = {
    "hydraulics": ["hydraulic", "boom", "cylinder", "hose", "steering", "blade", "leak"],
    "cooling_system": ["coolant", "cooling", "overheat", "radiator", "temperature", "temp ", "hot"],
    "operator": ["idle", "idling", "operator", "fuel waste"],
    "aftertreatment": ["dpf", "soot", "def ", "regen", "emission"],
    "engine": ["oil pressure", "engine", "rpm"],
}
CANDIDATES = 40


@dataclass
class Filters:
    asset_tags: list[str] = field(default_factory=list)
    subsystem: str | None = None
    status: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    safety_only: bool = False


@dataclass
class Memory:
    ref: str                   # "I12" (inference) or "O45" (observation/note)
    owner_type: str
    owner_id: int
    asset_tag: str
    content: str
    similarity: float
    score: float
    when: datetime | None
    meta: dict[str, Any] = field(default_factory=dict)


def infer_filters(question: str, known_tags: list[str], explicit: Filters | None = None) -> Filters:
    f = Filters(**vars(explicit)) if explicit else Filters()
    q = question.lower()
    if not f.asset_tags:
        for tag in known_tags:
            # "EX-320-A", "320-A", "320 A", "ex320a" all match EX-320-A
            core = tag.split("-", 1)[1].lower() if "-" in tag else tag.lower()
            patterns = [tag.lower(), core, core.replace("-", " "), tag.lower().replace("-", "")]
            if any(re.search(rf"(?<![\w-]){re.escape(p)}(?![\w])", q) for p in patterns):
                f.asset_tags.append(tag)
    if f.subsystem is None:
        hits = [s for s, words in SUBSYSTEM_WORDS.items() if any(w in q for w in words)]
        if len(hits) == 1:
            f.subsystem = hits[0]
    if any(w in q for w in ("safety", "unsafe", "dangerous", "escalat")):
        f.safety_only = True
    if f.status is None:
        for status in ("contested", "confirmed", "superseded"):
            if status in q:
                f.status = status
    return f


def retrieve(db: Session, embedder: Embedder, question: str, filters: Filters, *, k: int = 8) -> list[Memory]:
    vec = embedder.embed([question])[0]
    distance = Embedding.embedding.cosine_distance(vec)
    query = (select(Embedding, Asset.asset_tag, distance.label("distance"))
             .join(Asset, Asset.id == Embedding.asset_id)
             .order_by(distance).limit(CANDIDATES))
    if filters.asset_tags:
        query = query.where(Asset.asset_tag.in_(filters.asset_tags))
    rows = db.execute(query).all()

    inf_ids = [e.owner_id for e, _, _ in rows if e.owner_type == "inference"]
    obs_ids = [e.owner_id for e, _, _ in rows if e.owner_type == "observation"]
    infs = {i.id: i for i in db.scalars(select(Inference).where(Inference.id.in_(inf_ids)))} if inf_ids else {}
    obss = {o.id: o for o in db.scalars(select(RawObservation).where(RawObservation.id.in_(obs_ids)))} if obs_ids else {}

    memories = []
    for emb, tag, dist in rows:
        sim = 1.0 - float(dist)
        score = sim
        if emb.owner_type == "inference":
            inf = infs.get(emb.owner_id)
            if inf is None:
                continue
            when = inf.window_end
            if filters.status and inf.status != filters.status:
                continue
            if filters.safety_only and not inf.is_safety_critical:
                continue
            if filters.subsystem:
                score += 0.15 if inf.subsystem == filters.subsystem else -0.1
            score += 0.05 if inf.status in LIVE else -0.05  # prefer current beliefs over history
            meta = {"status": inf.status, "subsystem": inf.subsystem, "hypothesis": inf.hypothesis,
                    "version": inf.version, "final_confidence": inf.final_confidence,
                    "is_safety_critical": inf.is_safety_critical, "escalated": inf.escalated,
                    "title": inf.title}
            ref = f"I{inf.id}"
        else:
            obs = obss.get(emb.owner_id)
            if obs is None or filters.status:  # status filters only apply to inferences
                continue
            if filters.safety_only and not any(t in (obs.text or "").lower() for t in SAFETY_TERMS):
                continue
            when = obs.observed_at
            if filters.subsystem:
                words = SUBSYSTEM_WORDS.get(filters.subsystem, [])
                score += 0.1 if any(w in (obs.text or "").lower() for w in words) else -0.1
            meta = {"kind": obs.kind}
            ref = f"O{obs.id}"
        if filters.date_from and when and when < filters.date_from:
            continue
        if filters.date_to and when and when > filters.date_to:
            continue
        memories.append(Memory(ref, emb.owner_type, emb.owner_id, tag, emb.content, round(sim, 4),
                               round(score, 4), when, meta))
    memories.sort(key=lambda m: m.score, reverse=True)
    return memories[:k]
