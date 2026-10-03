from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset, RawObservation
from app.ingest.parsers import ObservationIn, ParseError


def store_observations(db: Session, records: Iterable[ObservationIn], *, source: str) -> list[RawObservation]:
    """Insert parsed records, resolving asset tags. Caller owns the transaction."""
    records = list(records)
    tags = {r.asset_tag for r in records}
    asset_ids = dict(db.execute(select(Asset.asset_tag, Asset.id).where(Asset.asset_tag.in_(tags))).all())
    unknown = tags - asset_ids.keys()
    if unknown:
        raise ParseError(f"unknown asset tags: {sorted(unknown)}")

    rows = [
        RawObservation(
            asset_id=asset_ids[r.asset_tag],
            observed_at=r.observed_at,
            kind=r.kind,
            source=source,
            payload=r.payload,
            text=r.text,
        )
        for r in records
    ]
    db.add_all(rows)
    db.flush()
    return rows
