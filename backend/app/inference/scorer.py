"""Confidence math. All weights come from config/scoring.yaml."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app import config
from app.inference.verifier import VerifiedClaim


@dataclass
class ConfidenceBreakdown:
    evidence_score: float
    llm_confidence_raw: float
    llm_confidence_verified: float
    base_rate: float
    final_confidence: float
    weights: dict[str, float]
    unverified_claims: int

    def as_dict(self) -> dict:
        return asdict(self)


def verified_llm_confidence(raw: float, claims: list[VerifiedClaim]) -> tuple[float, int]:
    """Raw confidence minus a penalty per unverified claim; zero if nothing was verified."""
    penalty = config.scoring()["unverified_claim_penalty"]
    raw = min(max(raw, 0.0), 1.0)
    unverified = sum(1 for c in claims if not c.verified)
    if not any(c.verified for c in claims):
        return 0.0, unverified
    return max(0.0, raw - penalty * unverified), unverified


def blend(evidence_score: float, llm_raw: float, claims: list[VerifiedClaim],
          base_rate: float, *, llm_used: bool = True) -> ConfidenceBreakdown:
    """final = w_e * evidence + w_l * llm_verified + w_h * base_rate.

    With llm_used=False (rule-based proposer, no Claude), the "LLM" confidence would just
    echo the evidence score, so its weight is redistributed proportionally instead."""
    w = dict(config.scoring()["confidence_weights"])
    if not llm_used:
        rest = w["evidence"] + w["history"]
        w = {"evidence": w["evidence"] / rest, "llm": 0.0, "history": w["history"] / rest}
        llm_raw = 0.0
    llm_verified, unverified = verified_llm_confidence(llm_raw, claims)
    final = w["evidence"] * evidence_score + w["llm"] * llm_verified + w["history"] * base_rate
    return ConfidenceBreakdown(
        evidence_score=round(evidence_score, 4),
        llm_confidence_raw=round(min(max(llm_raw, 0.0), 1.0), 4),
        llm_confidence_verified=round(llm_verified, 4),
        base_rate=round(base_rate, 4),
        final_confidence=round(final, 4),
        weights={k: round(v, 4) for k, v in w.items()},
        unverified_claims=unverified,
    )
