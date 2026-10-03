"""Evidence from free-text notes: symptoms, physical evidence, and repairs.

Keyword-based on purpose: it is deterministic and auditable. Claude reads the same
notes during interpretation, but only the engine's reading counts toward evidence_score.
"""

from __future__ import annotations

import re

import pandas as pd

from app.db.enums import EvidenceKind, Hypothesis
from app.evidence.rules import beyond, limits_for, metric_meta
from app.evidence.types import PROBLEM_EFFECTS, AssetContext, Finding

H = Hypothesis

SUBSYSTEM_TERMS: dict[str, list[str]] = {
    "cooling_system": ["coolant", "radiator", "temp gauge", "overheat", "fan", "thermostat", "cooler"],
    "hydraulics": ["hydraulic", "boom", "cylinder", "hose", "blade", "steering", "implement pump", "bucket"],
    "operator": ["idling", "idle time", "left running", "operator assigned", "trainee"],
    "aftertreatment": ["dpf", "def ", "regeneration", "soot"],
    "electrical": ["battery", "alternator", "wiring"],
}
SUBSYSTEM_METRICS = {
    "cooling_system": ["coolant_temp_max_c"],
    "hydraulics": ["hydraulic_pressure_bar", "hydraulic_oil_temp_c"],
}
SYMPTOMS = ["warning", "higher than usual", "creeping", "slow", "lazy", "heavy", "loud", "flashed",
            "low again"]
PHYSICAL = ["leak", "drips", "wet", "oil film", "residue", "topped up", "packed", "bent fins", "corroded"]
REPAIR = re.compile(r"\b(replaced|repaired|rebuilt|resealed|cleaned|flushed|overhauled)\b", re.I)
SAFETY_TERMS = ["steering", "brake", "fire", "smoke"]
NEGATION = re.compile(r"\b(no|not|without|never)\b(?:\W+\w+){0,3}\W+$", re.I)


def subsystems_in(text: str) -> list[str]:
    t = text.lower()
    return [s for s, terms in SUBSYSTEM_TERMS.items() if any(term in t for term in terms)]


def _present(text: str, terms: list[str]) -> list[str]:
    """Terms found in text, skipping ones preceded by a nearby negation ("no leaks")."""
    t = text.lower()
    hits = []
    for term in terms:
        for m in re.finditer(re.escape(term), t):
            if not NEGATION.search(t[: m.start()]):
                hits.append(term)
                break
    return hits


def check_notes(ctx: AssetContext) -> list[Finding]:
    """Symptom and physical-evidence findings from notes in the window."""
    n = ctx.notes
    window = n[(n.observed_at >= ctx.window_start) & (n.observed_at <= ctx.as_of)]
    findings = []
    for _, note in window.iterrows():
        text = note.text or ""
        if REPAIR.search(text):
            continue  # handled by check_repairs
        physical, symptoms = _present(text, PHYSICAL), _present(text, SYMPTOMS)
        for subsystem in subsystems_in(text):
            is_operator = subsystem == "operator"
            if not (physical or symptoms or is_operator):
                continue
            strength = 0.5 if physical else 0.35
            findings.append(Finding(
                key=f"note:{int(note.id)}:{subsystem}",
                kind=EvidenceKind.note,
                subsystem=subsystem,
                description=f"{note.kind.replace('_', ' ').capitalize()} ({note.author_role or 'unknown'}): "
                            f"\"{text}\"",
                strength=strength,
                effects={H.operating_practice: 1.0} if is_operator else PROBLEM_EFFECTS,
                observation_ids=[int(note.id)],
                safety=bool(_present(text, SAFETY_TERMS)) and bool(physical or symptoms),
                details={"physical_evidence": physical, "symptoms": symptoms},
            ))
    return findings


def check_repairs(ctx: AssetContext) -> list[Finding]:
    """Repairs recorded in maintenance notes, judged by what the readings did afterwards."""
    n = ctx.notes
    notes = n[(n.observed_at <= ctx.as_of) & (n.kind == "maintenance_note")]
    findings = []
    for _, note in notes.iterrows():
        text = note.text or ""
        if not REPAIR.search(text):
            continue
        # Only recent repairs matter; older ones are already reflected in later data.
        if (ctx.as_of - note.observed_at).days > 21:
            continue
        for subsystem in subsystems_in(text):
            metrics = SUBSYSTEM_METRICS.get(subsystem, [])
            t = ctx.telematics
            after = t[(t.observed_at > note.observed_at) & (t.observed_at <= ctx.as_of)]
            bad = pd.Series(False, index=after.index)
            for m in metrics:
                meta, lim = metric_meta(m), limits_for(ctx.asset_type, m)
                bad |= after[m].apply(lambda v: beyond(v, lim["warn"], meta["direction"]))

            if len(after) >= 2 and not bad.any():
                desc = (f"Repair recorded ({note.observed_at:%Y-%m-%d}): \"{text}\" Followed by "
                        f"{len(after)} shifts with normal readings.")
                strength, effects = 0.85, {H.resolved: 1.0, H.degradation: -0.9, H.acute_failure: -0.9,
                                           H.sensor_fault: -0.3}
            elif after.empty or (len(after) < 2 and not bad.any()):
                desc = (f"Repair recorded ({note.observed_at:%Y-%m-%d}): \"{text}\" Not enough "
                        f"readings since to confirm it worked.")
                strength, effects = 0.5, {H.resolved: 1.0, H.degradation: -0.5, H.acute_failure: -0.5}
            else:
                desc = (f"Repair recorded ({note.observed_at:%Y-%m-%d}): \"{text}\" But "
                        f"{int(bad.sum())} of {len(after)} shifts since are still beyond limits.")
                strength, effects = 0.7, {H.resolved: -1.0, H.degradation: 0.4}

            findings.append(Finding(
                key=f"repair:{int(note.id)}:{subsystem}",
                kind=EvidenceKind.maintenance,
                subsystem=subsystem,
                description=desc,
                strength=strength,
                effects=effects,
                observation_ids=[int(note.id)] + [int(i) for i in after["id"]],
                details={"repair_at": note.observed_at.isoformat(), "readings_after": len(after),
                         "readings_after_beyond_limits": int(bad.sum())},
            ))
    return findings
