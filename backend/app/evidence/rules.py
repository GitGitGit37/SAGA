"""Threshold rule checks against configured operating limits."""

from __future__ import annotations

from app import config
from app.db.enums import EvidenceKind, Hypothesis
from app.evidence.types import PROBLEM_EFFECTS, AssetContext, Finding

H = Hypothesis


def metric_meta(metric: str) -> dict:
    return config.thresholds()["metrics"][metric]


def limits_for(asset_type: str, metric: str) -> dict:
    return config.thresholds()["asset_types"][asset_type][metric]


def beyond(value: float, limit: float, direction: str) -> bool:
    return value > limit if direction == "high" else value < limit


def check_thresholds(ctx: AssetContext) -> list[Finding]:
    window = ctx.window_telematics
    findings = []
    for metric, meta in config.thresholds()["metrics"].items():
        if metric not in window:
            continue
        lim = limits_for(ctx.asset_type, metric)
        d = meta["direction"]
        warn = window[window[metric].apply(lambda v: beyond(v, lim["warn"], d))]
        if warn.empty:
            continue
        crit = warn[warn[metric].apply(lambda v: beyond(v, lim["critical"], d))]
        n_warn, n_crit = len(warn) - len(crit), len(crit)
        strength = min(1.0, (n_warn + 2 * n_crit) / 6)
        worst = warn[metric].max() if d == "high" else warn[metric].min()

        subsystem = meta["subsystem"]
        effects = {H.operating_practice: 1.0} if subsystem == "operator" else {
            **PROBLEM_EFFECTS, H.environmental: 0.3}
        findings.append(Finding(
            key=f"threshold:{metric}",
            kind=EvidenceKind.threshold,
            subsystem=subsystem,
            metric=metric,
            description=(
                f"{meta['label']} beyond warning limit ({lim['warn']} {meta['unit']}) in "
                f"{len(warn)} of {len(window)} shifts in the window, {n_crit} beyond critical "
                f"({lim['critical']} {meta['unit']}); worst {worst:g} {meta['unit']}."
            ),
            strength=strength,
            effects=effects,
            observation_ids=[int(i) for i in warn["id"]],
            source_ref=f"config:thresholds.{ctx.asset_type}.{metric}",
            details={"warn": lim["warn"], "critical": lim["critical"], "n_warn_only": n_warn,
                     "n_critical": n_crit, "n_window": len(window), "worst": float(worst)},
        ))
    return findings
