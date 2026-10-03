"""Build AssetContexts straight from the seed generator, no database needed."""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

import pandas as pd
import pytest

from app.evidence.types import AssetContext
from seed import generate


@lru_cache
def _frames(include_holdout: bool) -> dict[str, pd.DataFrame]:
    data, _ = generate.generate(seed=42)
    tel = data.telematics + (data.holdout_telematics if include_holdout else [])
    faults = data.faults + (data.holdout_faults if include_holdout else [])
    notes = data.notes + (data.holdout_notes if include_holdout else [])

    # Fake observation ids, unique across kinds, like raw_observations.id
    next_id = iter(range(1, 10**7))
    t = pd.DataFrame(tel).assign(id=lambda d: [next(next_id) for _ in range(len(d))])
    f = pd.DataFrame(faults).assign(id=lambda d: [next(next_id) for _ in range(len(d))])
    n = pd.DataFrame(notes).assign(id=lambda d: [next(next_id) for _ in range(len(d))])
    for df in (t, f, n):
        df["observed_at"] = pd.to_datetime(df.pop("timestamp"), utc=True)
    return {"telematics": t, "faults": f, "notes": n}


def build_context(asset_tag: str, *, day: int = 59, window_days: int = 14,
                  include_holdout: bool = False) -> AssetContext:
    frames = _frames(include_holdout)
    spec = next(a for a in generate.FLEET if a.asset_tag == asset_tag)
    as_of = pd.Timestamp(generate.START + timedelta(days=day, hours=23, minutes=59)).tz_convert("UTC")
    window_start = as_of - pd.Timedelta(days=window_days)

    def own(df: pd.DataFrame, tag: str) -> pd.DataFrame:
        out = df[(df.asset_tag == tag) & (df.observed_at <= as_of)].drop(columns="asset_tag")
        return out.sort_values("observed_at").reset_index(drop=True)

    peers = {a.asset_tag: own(frames["telematics"], a.asset_tag)
             for a in generate.FLEET if a.site == spec.site and a.asset_tag != asset_tag}
    return AssetContext(
        asset_tag=asset_tag, asset_type=spec.asset_type, site=spec.site,
        as_of=as_of, window_start=window_start,
        telematics=own(frames["telematics"], asset_tag),
        faults=own(frames["faults"], asset_tag),
        notes=own(frames["notes"], asset_tag),
        peers=peers,
    )


@pytest.fixture
def ctx_for():
    return build_context
