"""Fault code lookup: severity, subsystem, typical causes."""

from __future__ import annotations

import pandas as pd

from app import config
from app.db.enums import EvidenceKind, Hypothesis
from app.evidence.types import PROBLEM_EFFECTS, AssetContext, Finding

H = Hypothesis
SEVERITY_STRENGTH = {1: 0.15, 2: 0.35, 3: 0.6, 4: 0.85}


def lookup(code: str) -> dict | None:
    return config.fault_codes().get(code)


def check_fault_codes(ctx: AssetContext, isolated_obs_times: list[pd.Timestamp] | None = None) -> list[Finding]:
    """One finding per distinct code in the window.

    `isolated_obs_times` are timestamps of telematics readings the anomaly module
    judged to be isolated spikes. Faults logged during those shifts are probably
    triggered by the same bad reading, so they lean toward sensor_fault instead.
    """
    f = ctx.faults
    window = f[(f.observed_at >= ctx.window_start) & (f.observed_at <= ctx.as_of)]
    isolated_obs_times = isolated_obs_times or []
    safety_categories = set(config.scoring()["safety"]["categories"])
    findings = []

    for code, events in window.groupby("code"):
        info = lookup(code)
        if info is None:
            findings.append(Finding(
                key=f"fault:{code}", kind=EvidenceKind.fault_code, subsystem="unknown",
                description=f"Unknown fault code {code} ({len(events)} events); not in lookup table.",
                strength=0.1, effects={H.degradation: 0.5},
                observation_ids=[int(i) for i in events["id"]], source_ref="config:fault_codes"))
            continue

        during_spike = events[events.observed_at.apply(
            lambda t: any(abs((t - s).total_seconds()) <= 8 * 3600 and t >= s for s in isolated_obs_times))]
        all_during_spikes = len(during_spike) == len(events)

        sev = int(info["severity"])
        strength = min(0.95, SEVERITY_STRENGTH[sev] + 0.05 * (len(events) - 1))
        if all_during_spikes:
            effects = {H.sensor_fault: 0.6, H.degradation: 0.2, H.acute_failure: 0.2}
            note = " All events coincide with isolated sensor spikes."
        else:
            effects = {**PROBLEM_EFFECTS, H.acute_failure: 1.0 if sev >= 4 else 0.6}
            note = ""

        findings.append(Finding(
            key=f"fault:{code}",
            kind=EvidenceKind.fault_code,
            subsystem=info["subsystem"],
            description=(f"Fault {code} ({info['description']}), severity {sev}, "
                         f"{len(events)} events in the window.{note}"),
            strength=strength,
            effects=effects,
            observation_ids=[int(i) for i in events["id"]],
            source_ref=f"config:fault_codes.{code}",
            safety=info["category"] in safety_categories and not all_during_spikes,
            details={"severity": sev, "events": len(events), "category": info["category"],
                     "typical_causes": info["typical_causes"],
                     "coincides_with_isolated_spikes": all_during_spikes},
        ))
    return findings
