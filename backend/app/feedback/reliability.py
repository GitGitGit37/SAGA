"""User reliability: moves when later data confirms or refutes what a user said."""

from __future__ import annotations

from app import config
from app.db.enums import DataVerdict, Hypothesis

H = Hypothesis
PROBLEM = {H.degradation, H.acute_failure}


def judge(*, endorses: str | None, rejects: str | None, original_hypothesis: str,
          new_hypothesis: str, new_evidence_score: float) -> DataVerdict | None:
    """Verdict on a past feedback item, given what later data concluded.

    A later "resolved" (a repair fixed it) vindicates both the original problem and anyone
    who said it was resolved."""
    if new_evidence_score < config.scoring()["feedback"]["verdict_min_evidence"]:
        return None
    truths = {new_hypothesis}
    if new_hypothesis == H.resolved and original_hypothesis in PROBLEM:
        truths.add(original_hypothesis)

    if endorses is not None:
        return DataVerdict.confirmed if endorses in truths else DataVerdict.refuted
    if rejects is not None:
        # Denying a problem that a later repair proves was real is wrong.
        return DataVerdict.refuted if rejects in truths else DataVerdict.confirmed
    return None


def updated_reliability(current: float, verdict: DataVerdict) -> float:
    lr = config.scoring()["feedback"]["reliability_learning_rate"]
    if verdict == DataVerdict.confirmed:
        return round(current + lr * (1.0 - current), 4)
    return round(current - lr * current, 4)
