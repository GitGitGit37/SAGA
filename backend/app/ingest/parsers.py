"""Parse uploaded files into observation records.

Accepted formats (the seed generator writes the same ones):
  - telematics CSV: one row per asset per shift, `asset_tag` + `timestamp` + metric columns
  - fault code JSON: list of {asset_tag, timestamp, code, occurrences?}
  - notes JSON: list of {asset_tag, timestamp, kind, author_role?, text, ...extra}
  - plain text: a single note; asset_tag and timestamp are supplied by the caller
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from app.db.enums import ObservationKind

NOTE_KINDS = {ObservationKind.maintenance_note, ObservationKind.inspection_note}


class ObservationIn(BaseModel):
    asset_tag: str
    observed_at: datetime
    kind: ObservationKind
    payload: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None

    @field_validator("observed_at")
    @classmethod
    def _require_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must include a timezone offset")
        return v


class ParseError(ValueError):
    pass


def parse_telematics_csv(data: bytes | str) -> list[ObservationIn]:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    df = pd.read_csv(io.StringIO(text))
    missing = {"asset_tag", "timestamp"} - set(df.columns)
    if missing:
        raise ParseError(f"telematics CSV missing columns: {sorted(missing)}")

    records = []
    for row in df.to_dict(orient="records"):
        tag, ts = row.pop("asset_tag"), row.pop("timestamp")
        payload = {k: (None if pd.isna(v) else v) for k, v in row.items()}
        records.append(ObservationIn(asset_tag=tag, observed_at=ts,
                                     kind=ObservationKind.telematics, payload=payload))
    return records


def parse_fault_codes_json(data: bytes | str) -> list[ObservationIn]:
    items = _load_json_list(data)
    records = []
    for item in items:
        item = dict(item)
        if "code" not in item:
            raise ParseError(f"fault record missing 'code': {item}")
        tag, ts = item.pop("asset_tag"), item.pop("timestamp")
        records.append(ObservationIn(asset_tag=tag, observed_at=ts,
                                     kind=ObservationKind.fault_code, payload=item))
    return records


def parse_notes_json(data: bytes | str) -> list[ObservationIn]:
    items = _load_json_list(data)
    records = []
    for item in items:
        item = dict(item)
        kind = ObservationKind(item.pop("kind", ObservationKind.inspection_note))
        if kind not in NOTE_KINDS:
            raise ParseError(f"note kind must be one of {sorted(NOTE_KINDS)}, got {kind}")
        tag, ts, text = item.pop("asset_tag"), item.pop("timestamp"), item.pop("text")
        records.append(ObservationIn(asset_tag=tag, observed_at=ts, kind=kind,
                                     payload=item, text=text))
    return records


def parse_text_note(text: str, *, asset_tag: str, observed_at: datetime,
                    kind: ObservationKind = ObservationKind.inspection_note) -> list[ObservationIn]:
    return [ObservationIn(asset_tag=asset_tag, observed_at=observed_at, kind=kind, text=text.strip())]


def _load_json_list(data: bytes | str) -> list[dict[str, Any]]:
    obj = json.loads(data)
    if not isinstance(obj, list):
        raise ParseError("expected a JSON list of records")
    return obj
