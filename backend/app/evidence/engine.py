"""Evidence Engine: deterministic analysis of one asset's data.

analyze(ctx) runs every check and groups findings into per-subsystem signals.
Each signal can then be scored under any hypothesis:

    evidence_score(h) = noisy_or(strength of findings supporting h)
                        * (1 - noisy_or(strength of findings contradicting h))
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.enums import Hypothesis
from app.evidence.anomaly import detect_isolated_spikes, detect_multivariate_anomalies, detect_trends
from app.evidence.environment import check_environment
from app.evidence.fault_codes import check_fault_codes
from app.evidence.maintenance import check_notes, check_repairs
from app.evidence.rules import check_thresholds
from app.evidence.types import AssetContext, Finding, score_hypothesis

H = Hypothesis
SIGNAL_MIN_SCORE = 0.3
PROBLEM_HYPOTHESES = [H.degradation, H.acute_failure, H.operating_practice, H.sensor_fault]


@dataclass
class HypothesisScore:
    hypothesis: str
    score: float
    supporting: list[Finding]
    contradicting: list[Finding]


@dataclass
class Signal:
    """A subsystem where the evidence says something is going on."""

    subsystem: str
    findings: list[Finding]
    safety: bool
    scores: dict[str, HypothesisScore] = field(default_factory=dict)

    def score(self, hypothesis: str) -> HypothesisScore:
        if hypothesis not in self.scores:
            s, sup, con = score_hypothesis(self.findings, hypothesis)
            self.scores[hypothesis] = HypothesisScore(hypothesis, s, sup, con)
        return self.scores[hypothesis]

    def best(self) -> HypothesisScore:
        return max((self.score(h) for h in Hypothesis), key=lambda s: s.score)

    @property
    def strength(self) -> float:
        return max(self.score(h).score for h in [*PROBLEM_HYPOTHESES, H.resolved])


@dataclass
class Analysis:
    ctx: AssetContext
    findings: list[Finding]
    signals: list[Signal]

    def signal(self, subsystem: str) -> Signal:
        """Signal for a subsystem, even one below the reporting threshold."""
        for s in self.signals:
            if s.subsystem == subsystem:
                return s
        found = [f for f in self.findings if f.subsystem == subsystem]
        return Signal(subsystem, found, any(f.safety for f in found))


def analyze(ctx: AssetContext) -> Analysis:
    isolated, spike_times = detect_isolated_spikes(ctx)
    trends = detect_trends(ctx)
    findings: list[Finding] = [
        *check_thresholds(ctx),
        *check_fault_codes(ctx, spike_times),
        *trends,
        *isolated,
        *detect_multivariate_anomalies(ctx),
        *check_environment(ctx, {f.metric for f in trends if f.metric}),
        *check_notes(ctx),
        *check_repairs(ctx),
    ]

    by_subsystem: dict[str, list[Finding]] = {}
    for f in findings:
        by_subsystem.setdefault(f.subsystem, []).append(f)

    signals = []
    for subsystem, group in by_subsystem.items():
        sig = Signal(subsystem, group, safety=any(f.safety for f in group))
        if sig.strength >= SIGNAL_MIN_SCORE or sig.safety:
            signals.append(sig)
    signals.sort(key=lambda s: s.strength, reverse=True)
    return Analysis(ctx, findings, signals)
