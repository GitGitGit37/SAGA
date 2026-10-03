"""Assign a category to each raw observation.

Telematics and fault codes are categorised deterministically. Free-text notes go to
Claude in one batch per call, with a keyword fallback when no API key is configured.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import config
from app.db.enums import ObservationKind
from app.db.models import Asset, RawObservation
from app.evidence.fault_codes import lookup
from app.evidence.maintenance import REPAIR, SAFETY_TERMS, subsystems_in
from app.evidence.rules import beyond, limits_for
from app.inference.proposer import SUBSYSTEM_CATEGORY
from app.inference.schemas import NoteCategorySet
from app.llm.client import LLMError, StructuredLLM

log = logging.getLogger(__name__)

NOTE_SYSTEM = """You categorise maintenance and inspection notes for heavy equipment. \
For each note, pick the single best category and give your confidence (0-1). Use \
maintenance_event when the note records work performed (service, repair, top-up), safety \
when it reports a risk to people (steering, brakes, fire), and routine_operation when \
nothing notable is reported."""


def categorize_telematics(obs: RawObservation, asset_type: str) -> tuple[str, float]:
    worst = None
    for metric, meta in config.thresholds()["metrics"].items():
        v = obs.payload.get(metric)
        if v is None:
            continue
        lim = limits_for(asset_type, metric)
        if beyond(v, lim["critical"], meta["direction"]):
            return SUBSYSTEM_CATEGORY.get(meta["subsystem"], "engine_health"), 0.95
        if worst is None and beyond(v, lim["warn"], meta["direction"]):
            worst = meta["subsystem"]
    if worst:
        return SUBSYSTEM_CATEGORY.get(worst, "engine_health"), 0.8
    return "routine_operation", 0.9


def categorize_fault(obs: RawObservation) -> tuple[str, float]:
    info = lookup(obs.payload.get("code", ""))
    return (info["category"], 1.0) if info else ("engine_health", 0.3)


def categorize_note_keywords(text: str) -> tuple[str, float]:
    t = text.lower()
    if any(term in t for term in SAFETY_TERMS):
        return "safety", 0.6
    if REPAIR.search(text) or "topped up" in t or "pm 250" in t or "performed" in t:
        return "maintenance_event", 0.7
    subs = subsystems_in(text)
    if subs:
        return SUBSYSTEM_CATEGORY.get(subs[0], "engine_health"), 0.5
    return "routine_operation", 0.4


def categorize_observations(db: Session, asset: Asset, llm: StructuredLLM | None) -> int:
    """Categorise this asset's uncategorised observations. Returns how many were updated."""
    pending = [o for o in asset.observations if o.category is None]
    notes = []
    for o in pending:
        if o.kind == ObservationKind.telematics:
            o.category, o.category_confidence = categorize_telematics(o, asset.asset_type)
        elif o.kind == ObservationKind.fault_code:
            o.category, o.category_confidence = categorize_fault(o)
        elif o.text:
            notes.append(o)

    if notes and llm is not None:
        try:
            prompt = "\n".join(f"[{o.id}] ({o.kind}) {o.text}" for o in notes)
            result = llm.parse(tier="fast", system=NOTE_SYSTEM, prompt=prompt,
                               schema=NoteCategorySet, effort="low", max_tokens=4000)
            by_id = {i.observation_id: i for i in result.items}
            for o in notes:
                if o.id in by_id:
                    o.category = by_id[o.id].category
                    o.category_confidence = min(max(by_id[o.id].confidence, 0.0), 1.0)
        except LLMError as exc:
            log.warning("note categorisation via Claude failed (%s); using keywords", exc)
    for o in notes:
        if o.category is None:
            o.category, o.category_confidence = categorize_note_keywords(o.text)
    return len(pending)
