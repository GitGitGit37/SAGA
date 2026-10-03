"""Embed inferences and notes into pgvector so they can be recalled by meaning."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import ObservationKind
from app.db.models import Asset, Embedding, Inference, RawObservation
from app.settings import get_settings

log = logging.getLogger(__name__)
NOTE_KINDS = [ObservationKind.maintenance_note, ObservationKind.inspection_note]


class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    def __init__(self) -> None:
        from fastembed import TextEmbedding  # heavy import, load lazily

        s = get_settings()
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        s.embedding_cache_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = s.embedding_model
        self._model = TextEmbedding(s.embedding_model, cache_dir=str(s.embedding_cache_dir))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]


@lru_cache
def get_embedder() -> Embedder:
    return FastEmbedder()


def inference_text(inf: Inference, asset: Asset) -> str:
    return (f"{asset.asset_tag} {asset.name} ({asset.asset_type}, {asset.site}). "
            f"{inf.subsystem} / {inf.category} / {inf.hypothesis.replace('_', ' ')}. "
            f"{inf.title}. {inf.interpretation} Recommended: {inf.recommended_action or '-'} "
            f"Status: {inf.status}{', safety-critical' if inf.is_safety_critical else ''}"
            f"{', escalated' if inf.escalated else ''}. Version {inf.version}.")


def note_text(obs: RawObservation, asset: Asset) -> str:
    role = obs.payload.get("author_role", "unknown")
    return f"{asset.asset_tag} {asset.name}. {obs.kind.replace('_', ' ')} by {role}: {obs.text}"


def sync_embeddings(db: Session, embedder: Embedder) -> int:
    """Embed new inferences/notes and re-embed ones whose text changed (e.g. status).
    Returns how many rows were (re)embedded. Caller owns the transaction."""
    existing = {(e.owner_type, e.owner_id): e for e in db.scalars(select(Embedding))}
    pending: list[tuple[str, int, int, str]] = []

    for inf, asset in db.execute(select(Inference, Asset).join(Asset, Asset.id == Inference.asset_id)):
        pending.append(("inference", inf.id, asset.id, inference_text(inf, asset)))
    for obs, asset in db.execute(select(RawObservation, Asset).join(Asset, Asset.id == RawObservation.asset_id)
                                 .where(RawObservation.kind.in_(NOTE_KINDS))):
        pending.append(("observation", obs.id, asset.id, note_text(obs, asset)))

    todo = [p for p in pending if (p[0], p[1]) not in existing or existing[(p[0], p[1])].content != p[3]]
    if not todo:
        return 0
    vectors = embedder.embed([p[3] for p in todo])
    for (owner_type, owner_id, asset_id, content), vec in zip(todo, vectors):
        row = existing.get((owner_type, owner_id))
        if row is None:
            db.add(Embedding(owner_type=owner_type, owner_id=owner_id, asset_id=asset_id, content=content,
                             embedding=vec, model=embedder.model_name))
        else:
            row.content, row.embedding, row.model = content, vec, embedder.model_name
    db.flush()
    log.info("embedded %d memories", len(todo))
    return len(todo)
