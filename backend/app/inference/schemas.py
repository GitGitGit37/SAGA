"""Structured output schemas Claude must fill. Validated with Pydantic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CategoryName = Literal[
    "routine_operation", "engine_health", "hydraulics", "operator_behavior",
    "environmental_condition", "maintenance_event", "safety", "electrical", "emissions",
]
HypothesisName = Literal[
    "degradation", "acute_failure", "sensor_fault", "operating_practice", "environmental", "resolved",
]
ClaimType = Literal[
    "threshold_exceeded",  # metric beyond its warning limit (cite telematics ids)
    "trend",               # metric shifted vs this asset's baseline
    "fault_code",          # a fault code occurred (give code; cite fault ids)
    "note",                # an operator/technician note says something (cite note ids)
    "peer_comparison",     # other machines on the site behave differently / the same
    "ambient",             # ambient temperature is / isn't elevated
    "isolated_spike",      # readings spike for one shift with normal neighbours
    "repair",              # a repair was recorded
]


class Claim(BaseModel):
    type: ClaimType
    statement: str = Field(description="One sentence stating the fact this claim relies on.")
    metric: str | None = Field(default=None, description="Telemetry metric name, if the claim is about one.")
    code: str | None = Field(default=None, description="Fault code, for fault_code claims.")
    observation_ids: list[int] = Field(default_factory=list,
                                       description="Ids of the observations that show it.")


class Proposal(BaseModel):
    subsystem: str = Field(description="Exactly one of the subsystems listed under SIGNALS.")
    category: CategoryName
    hypothesis: HypothesisName
    title: str = Field(description="Short headline, under 80 characters.")
    interpretation: str = Field(description="2-4 sentences: what is happening and why you think so.")
    recommended_action: str = Field(description="One concrete next step for the fleet team.")
    confidence: float = Field(description="Your confidence in this interpretation, 0 to 1.")
    claims: list[Claim]


class ProposalSet(BaseModel):
    proposals: list[Proposal]


class NoteCategory(BaseModel):
    observation_id: int
    category: CategoryName
    confidence: float


class NoteCategorySet(BaseModel):
    items: list[NoteCategory]
