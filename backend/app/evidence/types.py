from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.db.enums import EvidenceKind, Hypothesis

H = Hypothesis

# Default effect of a "something is wrong" finding on each hypothesis.
PROBLEM_EFFECTS: dict[str, float] = {H.degradation: 1.0, H.acute_failure: 0.6}

MAX_EVIDENCE = 0.98

TELEMETRY_METRICS = [
    "coolant_temp_max_c", "oil_pressure_kpa", "hydraulic_pressure_bar",
    "hydraulic_oil_temp_c", "engine_rpm_avg", "idle_pct", "fuel_rate_lph",
]


@dataclass
class AssetContext:
    """Everything the engine needs about one asset, as of one point in time.

    DataFrames carry the raw_observations id in an `id` column so every finding
    can cite the rows it came from.
      telematics: id, observed_at, ambient_temp_c, <TELEMETRY_METRICS>
      faults:     id, observed_at, code, occurrences
      notes:      id, observed_at, kind, text, author_role
      peers:      other assets on the same site -> telematics frame (same columns)
    """

    asset_tag: str
    asset_type: str
    site: str
    as_of: pd.Timestamp
    window_start: pd.Timestamp
    telematics: pd.DataFrame
    faults: pd.DataFrame
    notes: pd.DataFrame
    peers: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def window_telematics(self) -> pd.DataFrame:
        t = self.telematics
        return t[(t.observed_at >= self.window_start) & (t.observed_at <= self.as_of)]

    def reference_telematics(self, frame: pd.DataFrame | None = None) -> pd.DataFrame:
        """Baseline period: the earlier half of the history before the window
        (at least 20 readings), so a slow drift that began before the window
        doesn't contaminate its own baseline."""
        t = self.telematics if frame is None else frame
        before = t[t.observed_at < self.window_start].sort_values("observed_at")
        n = max(20, len(before) // 2)
        return before.head(n)


@dataclass
class Finding:
    """One piece of evidence produced by the engine."""

    key: str                                  # stable id, e.g. "threshold:coolant_temp_max_c"
    kind: EvidenceKind
    subsystem: str
    description: str
    strength: float                           # 0..1, how strong the evidence is on its own
    effects: dict[str, float]                 # hypothesis -> +k supports / -k contradicts
    observation_ids: list[int] = field(default_factory=list)
    source_type: str = "observation"          # observation | config | inference
    source_ref: str = ""
    metric: str | None = None
    safety: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def effect_on(self, hypothesis: str) -> float:
        return self.effects.get(hypothesis, 0.0)


def noisy_or(values: list[float]) -> float:
    p = 1.0
    for v in values:
        p *= 1.0 - min(max(v, 0.0), 1.0)
    return 1.0 - p


def score_hypothesis(findings: list[Finding], hypothesis: str) -> tuple[float, list[Finding], list[Finding]]:
    """evidence = noisy_or(supporting) * (1 - noisy_or(contradicting))."""
    sup = [f for f in findings if f.effect_on(hypothesis) > 0]
    con = [f for f in findings if f.effect_on(hypothesis) < 0]
    s = noisy_or([f.strength * f.effect_on(hypothesis) for f in sup])
    c = noisy_or([f.strength * -f.effect_on(hypothesis) for f in con])
    # Capped below 1: the engine never claims certainty.
    return float(min(MAX_EVIDENCE, s * (1.0 - c))), sup, con
