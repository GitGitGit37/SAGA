"""Load an AssetContext from the database."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.enums import ObservationKind
from app.db.models import Asset, RawObservation
from app.evidence.types import AssetContext

DEFAULT_WINDOW_DAYS = 14
NOTE_KINDS = [ObservationKind.maintenance_note, ObservationKind.inspection_note]


def _telematics_frame(rows: list[RawObservation]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["id", "observed_at"])
    df = pd.DataFrame([{"id": r.id, "observed_at": r.observed_at, **r.payload} for r in rows])
    df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)
    return df.sort_values("observed_at").reset_index(drop=True)


def load_context(db: Session, asset: Asset, *, as_of: datetime | None = None,
                 window_days: int = DEFAULT_WINDOW_DAYS) -> AssetContext:
    if as_of is None:
        as_of = db.scalar(select(func.max(RawObservation.observed_at))
                          .where(RawObservation.asset_id == asset.id))
    if as_of is None:
        raise ValueError(f"{asset.asset_tag} has no observations")

    obs = db.scalars(select(RawObservation)
                     .where(RawObservation.asset_id == asset.id, RawObservation.observed_at <= as_of)
                     .order_by(RawObservation.observed_at)).all()

    faults = pd.DataFrame(
        [{"id": o.id, "observed_at": o.observed_at, "code": o.payload.get("code"),
          "occurrences": o.payload.get("occurrences", 1)} for o in obs if o.kind == ObservationKind.fault_code],
        columns=["id", "observed_at", "code", "occurrences"])
    notes = pd.DataFrame(
        [{"id": o.id, "observed_at": o.observed_at, "kind": o.kind, "text": o.text,
          "author_role": o.payload.get("author_role")} for o in obs if o.kind in NOTE_KINDS],
        columns=["id", "observed_at", "kind", "text", "author_role"])
    for df in (faults, notes):
        df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True)

    peers = {}
    for peer in db.scalars(select(Asset).where(Asset.site == asset.site, Asset.id != asset.id)):
        rows = db.scalars(select(RawObservation)
                          .where(RawObservation.asset_id == peer.id,
                                 RawObservation.kind == ObservationKind.telematics,
                                 RawObservation.observed_at <= as_of)).all()
        peers[peer.asset_tag] = _telematics_frame(rows)

    as_of_ts = pd.Timestamp(as_of).tz_convert("UTC")
    return AssetContext(
        asset_tag=asset.asset_tag,
        asset_type=asset.asset_type,
        site=asset.site,
        as_of=as_of_ts,
        window_start=as_of_ts - timedelta(days=window_days),
        telematics=_telematics_frame([o for o in obs if o.kind == ObservationKind.telematics]),
        faults=faults,
        notes=notes,
        peers=peers,
    )
