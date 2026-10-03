"""Checks every claim Claude makes against the actual data.

A claim is verified only if the data shows it. Citing an observation id that doesn't
exist for this asset, or that isn't the right kind of record, fails verification.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import config
from app.evidence.engine import Analysis
from app.evidence.rules import beyond, limits_for
from app.inference.schemas import Claim


@dataclass
class VerifiedClaim:
    claim: Claim
    verified: bool
    reason: str
    finding_key: str | None = None


def verify_claims(claims: list[Claim], analysis: Analysis) -> list[VerifiedClaim]:
    return [verify_claim(c, analysis) for c in claims]


def verify_claim(claim: Claim, analysis: Analysis) -> VerifiedClaim:
    ctx = analysis.ctx
    tel_ids = set(ctx.telematics["id"].astype(int))
    fault_ids = set(ctx.faults["id"].astype(int)) if len(ctx.faults) else set()
    note_ids = set(ctx.notes["id"].astype(int)) if len(ctx.notes) else set()
    known = tel_ids | fault_ids | note_ids
    findings = {f.key: f for f in analysis.findings}

    def ok(reason: str, key: str | None = None) -> VerifiedClaim:
        return VerifiedClaim(claim, True, reason, key)

    def fail(reason: str) -> VerifiedClaim:
        return VerifiedClaim(claim, False, reason)

    missing = [i for i in claim.observation_ids if i not in known]
    if missing:
        return fail(f"cites observation ids that don't exist for {ctx.asset_tag}: {missing[:5]}")

    metrics = config.thresholds()["metrics"]
    match claim.type:
        case "threshold_exceeded":
            if claim.metric not in metrics:
                return fail(f"unknown metric {claim.metric!r}")
            lim = limits_for(ctx.asset_type, claim.metric)["warn"]
            d = metrics[claim.metric]["direction"]
            rows = ctx.window_telematics
            if claim.observation_ids:
                if any(i not in tel_ids for i in claim.observation_ids):
                    return fail("cites non-telematics records for a threshold claim")
                rows = ctx.telematics[ctx.telematics["id"].isin(claim.observation_ids)]
            hits = rows[rows[claim.metric].apply(lambda v: beyond(v, lim, d))]
            if hits.empty:
                return fail(f"no cited reading of {claim.metric} is beyond the warning limit {lim}")
            return ok(f"{len(hits)} reading(s) beyond {lim}", f"threshold:{claim.metric}")

        case "trend":
            key = f"trend:{claim.metric}"
            if key in findings:
                return ok("engine detected the same trend", key)
            return fail(f"no persistent trend in {claim.metric} vs this asset's baseline")

        case "fault_code":
            f = ctx.faults
            window = f[(f.observed_at >= ctx.window_start) & (f.observed_at <= ctx.as_of)]
            if claim.code is None or claim.code not in set(window["code"]):
                return fail(f"fault {claim.code} did not occur in the window")
            if claim.observation_ids:
                cited = f[f["id"].isin(claim.observation_ids)]
                if len(cited) != len(claim.observation_ids) or set(cited["code"]) != {claim.code}:
                    return fail(f"cited records are not {claim.code} fault events")
            return ok(f"fault {claim.code} present", f"fault:{claim.code}")

        case "note":
            if not claim.observation_ids:
                return fail("note claim cites no note")
            if any(i not in note_ids for i in claim.observation_ids):
                return fail("cites records that are not notes")
            return ok("cited notes exist")

        case "peer_comparison" | "ambient" | "isolated_spike" | "repair":
            prefix = {"peer_comparison": "peers:", "ambient": "ambient:",
                      "isolated_spike": "isolated:", "repair": "repair:"}[claim.type]
            matches = [k for k in findings if k.startswith(prefix)
                       and (claim.metric is None or claim.type == "repair" or k.endswith(claim.metric))]
            if matches:
                return ok("engine finding agrees", matches[0])
            return fail(f"engine found no {claim.type.replace('_', ' ')} evidence")

    return fail(f"unsupported claim type {claim.type}")
