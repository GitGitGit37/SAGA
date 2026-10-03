"""String enums stored as VARCHAR columns (no native PG enums, so migrations stay simple)."""

from enum import StrEnum


class AssetType(StrEnum):
    excavator = "excavator"
    wheel_loader = "wheel_loader"
    dozer = "dozer"


class UserRole(StrEnum):
    operator = "operator"
    technician = "technician"
    fleet_manager = "fleet_manager"


class ObservationKind(StrEnum):
    telematics = "telematics"
    fault_code = "fault_code"
    maintenance_note = "maintenance_note"
    inspection_note = "inspection_note"
    image = "image"


class InferenceStatus(StrEnum):
    active = "active"
    contested = "contested"
    superseded = "superseded"
    confirmed = "confirmed"


class EvidenceKind(StrEnum):
    threshold = "threshold"          # rule check against configured limits
    fault_code = "fault_code"        # fault code lookup
    anomaly = "anomaly"              # trend / anomaly detection
    base_rate = "base_rate"          # historical confirmation rate
    llm_claim = "llm_claim"          # a claim Claude made, with verification result
    maintenance = "maintenance"      # maintenance / repair record
    context = "context"              # user-supplied context backed by a record


class EvidenceDirection(StrEnum):
    supports = "supports"
    contradicts = "contradicts"


class FeedbackType(StrEnum):
    confirm = "confirm"
    reject = "reject"
    correct = "correct"
    add_context = "add_context"


class FeedbackOutcome(StrEnum):
    pending = "pending"
    applied = "applied"              # inference revised or confirmed because of it
    held = "held"                    # system kept its position; inference marked contested
    escalated = "escalated"          # safety-critical disagreement, flagged for humans
    recorded = "recorded"            # context logged, no score change


class DataVerdict(StrEnum):
    """Set later when new data confirms or refutes what a feedback item claimed."""
    confirmed = "confirmed"
    refuted = "refuted"
