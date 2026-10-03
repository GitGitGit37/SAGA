"""Ambient conditions and same-site peer comparison.

These are what let the system push back on "it's just the hot weather": if ambient
temperature isn't elevated and other machines on the same site are running normally,
the environment can't explain one machine's drift.
"""

from __future__ import annotations

import numpy as np

from app.db.enums import EvidenceKind, Hypothesis
from app.evidence.anomaly import baseline_stats
from app.evidence.rules import metric_meta
from app.evidence.types import AssetContext, Finding

H = Hypothesis
HEAT_SENSITIVE = ["coolant_temp_max_c", "hydraulic_oil_temp_c"]


def check_environment(ctx: AssetContext, trending_metrics: set[str]) -> list[Finding]:
    findings = []
    relevant = [m for m in HEAT_SENSITIVE if m in trending_metrics]
    if not relevant:
        return findings

    ref, window = ctx.reference_telematics(), ctx.window_telematics
    if "ambient_temp_c" in window and len(ref) and len(window):
        delta = float(window.ambient_temp_c.mean() - ref.ambient_temp_c.mean())
        for metric in relevant:
            subsystem = metric_meta(metric)["subsystem"]
            if delta > 3:
                findings.append(Finding(
                    key=f"ambient:{metric}", kind=EvidenceKind.environment, subsystem=subsystem,
                    metric="ambient_temp_c",
                    description=f"Ambient temperature is {delta:.1f} °C above this asset's baseline period, "
                                f"which could explain part of the {metric_meta(metric)['label'].lower()} rise.",
                    strength=min(0.8, 0.3 + 0.08 * delta),
                    effects={H.environmental: 1.0, H.degradation: -0.4},
                    source_ref="analysis:ambient", details={"ambient_delta_c": round(delta, 1)}))
            elif delta < 1.5:
                findings.append(Finding(
                    key=f"ambient:{metric}", kind=EvidenceKind.environment, subsystem=subsystem,
                    metric="ambient_temp_c",
                    description=f"Ambient temperature is not elevated (window mean {delta:+.1f} °C vs baseline), "
                                f"so weather does not explain the {metric_meta(metric)['label'].lower()} rise.",
                    strength=0.6,
                    effects={H.environmental: -1.0},
                    source_ref="analysis:ambient", details={"ambient_delta_c": round(delta, 1)}))

    for metric in relevant:
        peer_z = []
        for peer in ctx.peers.values():
            pref = ctx.reference_telematics(peer)
            pwin = peer[(peer.observed_at >= ctx.window_start) & (peer.observed_at <= ctx.as_of)]
            if len(pref) < 10 or pwin.empty:
                continue
            mu, sd = baseline_stats(pref, metric)
            peer_z.append((pwin[metric].mean() - mu) / sd)
        if len(peer_z) < 2:
            continue
        median_z = float(np.median(peer_z))
        subsystem = metric_meta(metric)["subsystem"]
        label = metric_meta(metric)["label"].lower()
        if median_z < 1.0:
            findings.append(Finding(
                key=f"peers:{metric}", kind=EvidenceKind.environment, subsystem=subsystem, metric=metric,
                description=f"{len(peer_z)} other machines at {ctx.site} show normal {label} over the same "
                            f"period (median z = {median_z:.1f}); the problem is specific to this machine.",
                strength=0.7,
                effects={H.environmental: -1.0, H.degradation: 0.5, H.acute_failure: 0.3},
                source_ref=f"analysis:peers.{ctx.site}",
                details={"peer_count": len(peer_z), "median_peer_z": round(median_z, 2)}))
        elif median_z > 2.0:
            findings.append(Finding(
                key=f"peers:{metric}", kind=EvidenceKind.environment, subsystem=subsystem, metric=metric,
                description=f"Other machines at {ctx.site} show the same {label} rise "
                            f"(median z = {median_z:.1f}), pointing to a site-wide cause.",
                strength=0.6,
                effects={H.environmental: 1.0, H.degradation: -0.4},
                source_ref=f"analysis:peers.{ctx.site}",
                details={"peer_count": len(peer_z), "median_peer_z": round(median_z, 2)}))
    return findings
