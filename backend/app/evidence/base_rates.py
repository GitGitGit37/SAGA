"""Historical base rates: how often inferences of this kind were confirmed on similar assets."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from app import config
from app.db.enums import InferenceStatus
from app.db.models import Asset, Inference


@dataclass
class BaseRate:
    value: float
    confirmed: int
    refuted: int
    prior: float
    prior_strength: float

    @property
    def description(self) -> str:
        if self.confirmed + self.refuted == 0:
            return f"No outcome history yet; using prior {self.prior:.2f}."
        return (f"{self.confirmed} confirmed / {self.refuted} refuted on similar assets "
                f"(Beta prior {self.prior:.2f}, strength {self.prior_strength:g}) → {self.value:.2f}.")


def beta_mean(confirmed: int, refuted: int, prior: float | None = None,
              strength: float | None = None) -> BaseRate:
    cfg = config.scoring()["base_rate"]
    prior = cfg["prior"] if prior is None else prior
    strength = cfg["prior_strength"] if strength is None else strength
    value = (confirmed + prior * strength) / (confirmed + refuted + strength)
    return BaseRate(value, confirmed, refuted, prior, strength)


def lookup_base_rate(db: Session, *, category: str, hypothesis: str, asset_type: str) -> BaseRate:
    """confirmed: lineages with a version marked confirmed.
    refuted: lineages whose later refinement version switched to a different hypothesis."""
    similar = (select(Inference.lineage_id)
               .join(Asset, Asset.id == Inference.asset_id)
               .where(Inference.category == category, Inference.hypothesis == hypothesis,
                      Asset.asset_type == asset_type))

    confirmed = db.scalar(
        select(func.count(func.distinct(Inference.lineage_id)))
        .where(Inference.lineage_id.in_(similar), Inference.status == InferenceStatus.confirmed)) or 0

    later = aliased(Inference)
    refuted = db.scalar(
        select(func.count(func.distinct(Inference.lineage_id)))
        .join(later, and_(later.lineage_id == Inference.lineage_id, later.version > Inference.version))
        .where(Inference.lineage_id.in_(similar), Inference.hypothesis == hypothesis,
               later.created_by == "refinement", later.hypothesis != hypothesis)) or 0

    return beta_mean(confirmed, refuted)
