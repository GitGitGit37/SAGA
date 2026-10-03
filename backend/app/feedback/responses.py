"""The system's reply to feedback, especially when it holds its position.

Claude (reasoning tier) writes the reply from structured facts and must cite evidence by
label. If it cites anything it wasn't given, or isn't available, a deterministic template
is used instead.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app.evidence.maintenance import REPAIR
from app.feedback.refinement import Action, Decision, FeedbackIn
from app.llm.client import LLMError, StructuredLLM

log = logging.getLogger(__name__)

# What would separate the current hypothesis from the one the user proposes.
SETTLING_DATA: dict[tuple[str, str], str] = {
    ("degradation", "environmental"): "readings from a cooler shift at normal load, or a radiator/cooler "
                                      "inspection with the side screens removed",
    ("degradation", "sensor_fault"): "a handheld temperature or pressure reading taken next to the sensor "
                                     "during a working shift, or a sensor/connector test",
    ("degradation", "operating_practice"): "a shift with a different operator at comparable load",
    ("degradation", "resolved"): "the maintenance record for the repair and a few shifts of readings after it",
    ("acute_failure", "sensor_fault"): "a sensor/connector test and a manual gauge reading",
    ("operating_practice", "degradation"): "idle-time and load data from a different operator on this machine",
    ("sensor_fault", "degradation"): "a manual measurement during a shift where the sensor reads high",
    ("resolved", "degradation"): "readings from the next few shifts at normal load",
}
DEFAULT_SETTLING = "an inspection record or a few more shifts of readings under comparable conditions"


class EvidenceRef(BaseModel):
    label: str          # e.g. "E3"
    description: str
    direction: str      # supports | contradicts
    strength: float


class HoldReply(BaseModel):
    message: str = Field(description="Reply to the user, 3-6 sentences, citing evidence as [E#].")
    cited_evidence: list[str] = Field(description="Labels of the evidence items cited, e.g. ['E1','E3'].")
    data_requests: list[str] = Field(description="Specific data that would settle the disagreement.")


SYSTEM = """You are Ride Along, a maintenance memory system for heavy equipment, replying to \
a person who gave feedback on one of your inferences. The decision has already been made by \
the evidence engine; you are explaining it, not re-deciding it.

Be direct and respectful. Explain why the system did what it did, citing the evidence items \
provided as [E#]. Cite only labels from the list. When the system holds its position, say \
plainly that feedback is weighed as evidence and why this feedback did not outweigh the data, \
and ask for the specific data that would settle the question. Never claim facts that are not \
in the provided material. If the item is safety-critical and escalated, say that the warning \
stays active and a supervisor will review it; humans decide what to do with the machine."""


def settling_data(current: str, alternative: str | None) -> str:
    return SETTLING_DATA.get((current, alternative or ""), DEFAULT_SETTLING)


def mentions_unattached_record(fb: FeedbackIn) -> bool:
    """Rationale claims new work was done but nothing verifiable was attached."""
    return bool(fb.rationale and REPAIR.search(fb.rationale) and not fb.has_new_evidence)


def template_reply(decision: Decision, fb: FeedbackIn, evidence: list[EvidenceRef], *,
                   title: str, rerun_note: str | None = None) -> HoldReply:
    sup = [e for e in evidence if e.direction == "supports"][:3]
    cites = " ".join(f"[{e.label}] {e.description}" for e in sup)
    cur, alt = decision.current_hypothesis, decision.alternative
    parts: list[str] = []
    requests: list[str] = []

    if rerun_note:
        parts.append(rerun_note)

    match decision.action:
        case Action.hold | Action.escalate:
            parts.append(f"I'm keeping \"{title}\" ({cur.replace('_', ' ')}) and have marked it contested.")
            parts.append(f"Your feedback counts as evidence with weight {decision.weight.explain(fb.role)}.")
            if cites:
                parts.append(f"The data behind the current reading: {cites}")
            if alt:
                parts.append(f"For '{alt.replace('_', ' ')}' to replace it, it would need to beat "
                             f"{decision.combined[cur]:.2f} by more than {decision.margin}; it scores "
                             f"{decision.combined[alt]:.2f}.")
            requests.append(settling_data(cur, alt))
            if decision.action == Action.escalate:
                parts.append("This item is safety-critical, so the warning stays active and it has been "
                             "escalated for a supervisor to review. People decide what to do with the machine; "
                             "the system just won't quietly change its assessment.")
        case Action.revise:
            parts.append(f"Thanks. The combined evidence now favours '{(alt or cur).replace('_', ' ')}', "
                         f"so I've created a new version. {' '.join(decision.reasons)}")
        case Action.confirm:
            parts.append(f"Thanks, marked confirmed. Your confirmation counted with weight "
                         f"{decision.weight.explain(fb.role)}.")
        case Action.record:
            parts.append("Thanks, recorded as context on this inference. " + " ".join(decision.reasons))

    if mentions_unattached_record(fb):
        parts.append("You mention work that was done. If you attach the maintenance record, I'll re-run the "
                     "analysis with it; a record is verifiable in a way a description isn't.")
        requests.insert(0, "the maintenance record for the work you describe")

    return HoldReply(message=" ".join(parts), cited_evidence=[e.label for e in sup], data_requests=requests)


def write_reply(decision: Decision, fb: FeedbackIn, evidence: list[EvidenceRef], *, title: str,
                interpretation: str, llm: StructuredLLM | None, rerun_note: str | None = None) -> HoldReply:
    fallback = template_reply(decision, fb, evidence, title=title, rerun_note=rerun_note)
    if llm is None or decision.action not in (Action.hold, Action.escalate):
        return fallback

    labels = {e.label for e in evidence}
    prompt = "\n".join([
        f"INFERENCE: {title} (hypothesis: {decision.current_hypothesis})",
        f"INTERPRETATION: {interpretation}",
        f"FEEDBACK ({fb.role}, {fb.feedback_type}): {fb.rationale or '(no rationale)'}",
        f"PROPOSED ALTERNATIVE: {decision.alternative or 'none'}",
        f"FEEDBACK WEIGHT: {decision.weight.explain(fb.role)}",
        f"COMBINED SCORES: {decision.combined}  (revision margin {decision.margin})",
        f"DECISION: {decision.action} - {' '.join(decision.reasons)}",
        f"ATTACHED NEW RECORDS: {'yes' if fb.has_new_evidence else 'no'}",
        f"NOTE FROM RE-RUN: {rerun_note or 'n/a'}",
        f"DATA THAT WOULD SETTLE IT: {settling_data(decision.current_hypothesis, decision.alternative)}",
        "EVIDENCE:",
        *[f"[{e.label}] ({e.direction}, strength {e.strength:.2f}) {e.description}" for e in evidence],
    ])
    try:
        reply = llm.parse(tier="reasoning", system=SYSTEM, prompt=prompt, schema=HoldReply,
                          effort="medium", max_tokens=4000)
    except LLMError as exc:
        log.warning("reply generation failed (%s); using template", exc)
        return fallback

    in_text = set(re.findall(r"\[(E\d+)\]", reply.message))
    if not reply.message.strip() or not (set(reply.cited_evidence) | in_text) <= labels:
        log.warning("reply cited evidence it wasn't given; using template")
        return fallback
    if mentions_unattached_record(fb) and fallback.data_requests[0] not in reply.data_requests:
        reply.data_requests.insert(0, fallback.data_requests[0])
    return reply
