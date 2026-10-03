"""Trend and anomaly detection over an asset's own history.

- Trend: window mean vs the asset's reference baseline (z-score), plus persistence
  (share of window readings beyond baseline + 2 sigma). Persistent shifts are real;
  a single outlier is not a trend.
- Isolated spikes: alarm-level readings whose neighbours are normal. These point at a
  sensor problem rather than the machine, and contradict "degradation".
- IsolationForest: multivariate anomaly share in the window, trained on the baseline.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from app.db.enums import EvidenceKind, Hypothesis
from app.evidence.rules import beyond, limits_for, metric_meta
from app.evidence.types import PROBLEM_EFFECTS, TELEMETRY_METRICS, AssetContext, Finding

H = Hypothesis
MIN_BASELINE = 10
TREND_Z = 1.5
TREND_PERSISTENCE = 0.4
SPIKE_MIN_Z = 4.0  # a sensor glitch is wild, not a mild outlier


def _signed(direction: str) -> int:
    return 1 if direction == "high" else -1


def baseline_stats(ref: pd.DataFrame, metric: str) -> tuple[float, float]:
    mu = float(ref[metric].mean())
    sd = float(ref[metric].std(ddof=1))
    return mu, max(sd, 1e-6)


def detect_trends(ctx: AssetContext) -> list[Finding]:
    ref, window = ctx.reference_telematics(), ctx.window_telematics
    if len(ref) < MIN_BASELINE or window.empty:
        return []
    findings = []
    for metric in TELEMETRY_METRICS:
        meta = metric_meta(metric)
        sign = _signed(meta["direction"])
        mu, sd = baseline_stats(ref, metric)
        z = sign * (window[metric].mean() - mu) / sd
        shifted = window[sign * (window[metric] - mu) / sd > 2]
        persistence = len(shifted) / len(window)
        if z < TREND_Z or persistence < TREND_PERSISTENCE:
            continue

        days = (window.observed_at - window.observed_at.min()).dt.total_seconds() / 86400
        slope = float(np.polyfit(days, window[metric], 1)[0]) if len(window) >= 3 else 0.0
        strength = min(1.0, 0.3 + 0.15 * z) * persistence ** 0.5
        effects = ({H.operating_practice: 1.0} if meta["subsystem"] == "operator"
                   else {H.degradation: 1.0, H.acute_failure: 0.5, H.environmental: 0.3})
        findings.append(Finding(
            key=f"trend:{metric}",
            kind=EvidenceKind.anomaly,
            subsystem=meta["subsystem"],
            metric=metric,
            description=(
                f"{meta['label']} shifted {'up' if window[metric].mean() > mu else 'down'} "
                f"vs this asset's baseline: window mean {window[metric].mean():.1f} vs baseline "
                f"{mu:.1f} ± {sd:.1f} {meta['unit']} (z = {z:.1f}); {persistence:.0%} of window shifts "
                f"beyond 2σ; slope {slope:+.2f} {meta['unit']}/day."
            ),
            strength=strength,
            effects=effects,
            observation_ids=[int(i) for i in shifted["id"]],
            source_ref=f"analysis:trend.{metric}",
            source_type="observation",
            details={"z": round(float(z), 2), "baseline_mean": round(mu, 2), "baseline_sd": round(sd, 2),
                     "window_mean": round(float(window[metric].mean()), 2),
                     "persistence": round(persistence, 2), "slope_per_day": round(slope, 3)},
        ))
    return findings


def detect_isolated_spikes(ctx: AssetContext) -> tuple[list[Finding], list[pd.Timestamp]]:
    """Alarm-level readings whose previous and next readings are both normal.

    Looks across the asset's whole history up to as_of, because a recurring isolated
    spike pattern is only visible over time. Returns findings plus the spike timestamps
    (used to attribute coincident fault codes)."""
    t = ctx.telematics.sort_values("observed_at").reset_index(drop=True)
    ref = ctx.reference_telematics()
    if len(ref) < MIN_BASELINE or len(t) < 3:
        return [], []

    findings, spike_times = [], []
    for metric in TELEMETRY_METRICS:
        meta = metric_meta(metric)
        if meta["subsystem"] == "operator":
            continue
        warn = limits_for(ctx.asset_type, metric)["warn"]
        mu, sd = baseline_stats(ref, metric)
        sign = _signed(meta["direction"])

        def normal(v: float) -> bool:
            return sign * (v - mu) / sd < 2 and not beyond(v, warn, meta["direction"])

        spikes = [i for i in range(1, len(t) - 1)
                  if beyond(t.loc[i, metric], warn, meta["direction"])
                  and sign * (t.loc[i, metric] - mu) / sd >= SPIKE_MIN_Z
                  and normal(t.loc[i - 1, metric]) and normal(t.loc[i + 1, metric])]
        if not spikes:
            continue
        # The newest reading has no "next" neighbour yet. If it spikes after a normal reading
        # and this metric already has a history of isolated spikes, treat it as the same pattern.
        last = len(t) - 1
        if beyond(t.loc[last, metric], warn, meta["direction"]) \
                and sign * (t.loc[last, metric] - mu) / sd >= SPIKE_MIN_Z and normal(t.loc[last - 1, metric]):
            spikes.append(last)
        sustained = sum(1 for i in range(len(t)) if beyond(t.loc[i, metric], warn, meta["direction"])) - len(spikes)
        # If most exceedances are sustained, a few isolated ones don't indicate a sensor issue.
        if sustained > len(spikes):
            continue
        rows = t.loc[spikes]
        spike_times += list(rows.observed_at)
        findings.append(Finding(
            key=f"isolated:{metric}",
            kind=EvidenceKind.anomaly,
            subsystem=meta["subsystem"],
            metric=metric,
            description=(
                f"{len(spikes)} isolated {meta['label'].lower()} spike(s) "
                f"({', '.join(f'{v:g}' for v in rows[metric])} {meta['unit']}) with normal readings on the "
                f"shifts before and after; {sustained} sustained exceedances. Pattern fits a sensor or "
                f"wiring fault better than a real {meta['subsystem'].replace('_', ' ')} problem."
            ),
            strength=min(0.9, 0.4 + 0.15 * len(spikes)),
            effects={H.sensor_fault: 1.0, H.degradation: -0.7, H.acute_failure: 0.2, H.environmental: -0.5},
            observation_ids=[int(i) for i in rows["id"]],
            source_ref=f"analysis:isolated_spikes.{metric}",
            details={"spikes": len(spikes), "sustained_exceedances": sustained},
        ))
    return findings, spike_times


def detect_multivariate_anomalies(ctx: AssetContext, seed: int = 0) -> list[Finding]:
    """IsolationForest trained on the baseline; flags window rows that don't look like it.
    Anomalous rows are attributed to the subsystem of their most-deviant metric."""
    ref, window = ctx.reference_telematics(), ctx.window_telematics
    metrics = [m for m in TELEMETRY_METRICS if m in ref]
    if len(ref) < 20 or len(window) < 4:
        return []
    forest = IsolationForest(n_estimators=200, contamination=0.05, random_state=seed)
    forest.fit(ref[metrics].to_numpy())
    flagged = window[forest.predict(window[metrics].to_numpy()) == -1]
    if flagged.empty:
        return []

    stats = {m: baseline_stats(ref, m) for m in metrics}

    def driver(row: pd.Series) -> str:
        zs = {m: _signed(metric_meta(m)["direction"]) * (row[m] - stats[m][0]) / stats[m][1] for m in metrics}
        return max(zs, key=zs.get)

    flagged = flagged.assign(driver=flagged.apply(driver, axis=1))
    flagged = flagged.assign(subsystem=flagged.driver.map(lambda m: metric_meta(m)["subsystem"]))
    findings = []
    for subsystem, rows in flagged.groupby("subsystem"):
        share = len(rows) / len(window)
        if len(rows) < 3 or share < 0.25:
            continue
        effects = ({H.operating_practice: 0.5} if subsystem == "operator"
                   else {**PROBLEM_EFFECTS, H.environmental: 0.2})
        findings.append(Finding(
            key=f"iforest:{subsystem}",
            kind=EvidenceKind.anomaly,
            subsystem=subsystem,
            description=(f"Multivariate anomaly model flags {len(rows)} of {len(window)} window shifts "
                         f"({share:.0%}) as unlike this asset's baseline, driven mainly by "
                         f"{', '.join(sorted(set(rows.driver)))}."),
            strength=min(1.0, share) * 0.5,  # corroborating only; overlaps with trend findings
            effects=effects,
            observation_ids=[int(i) for i in rows["id"]],
            source_ref="analysis:isolation_forest",
            details={"flagged": len(rows), "window": len(window), "drivers": sorted(set(rows.driver))},
        ))
    return findings
