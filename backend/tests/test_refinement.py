"""Feedback & refinement. The three required properties:

  (a) weak feedback against strong evidence does NOT flip an inference
  (b) feedback with new verifiable evidence DOES
  (c) safety items escalate instead of being suppressed
"""

import pytest

from app.db.enums import DataVerdict
from app.evidence.base_rates import beta_mean
from app.evidence.engine import Signal, analyze
from app.evidence.types import Finding
from app.feedback.refinement import (Action, Belief, FeedbackIn, FeedbackRejected, PriorFeedback,
                                     decide)
from app.feedback.reliability import judge, updated_reliability
from app.feedback.responses import EvidenceRef, HoldReply, template_reply, write_reply

NO_HISTORY = lambda category, hypothesis: beta_mean(0, 0)  # noqa: E731


def belief_from(signal, *, category="engine_health", safety=None):
    best = signal.best()
    return Belief(best.hypothesis, category, signal.subsystem,
                  signal.safety if safety is None else safety, best.score)


def fb(kind, role="operator", reliability=0.5, **kw):
    return FeedbackIn(feedback_type=kind, role=role, reliability=reliability, **kw)


# --------------------------------------------------------------------------- #
# (a) weak feedback vs strong evidence
# --------------------------------------------------------------------------- #

def test_a_hot_weather_claim_does_not_flip_radiator_blockage(ctx_for):
    signal = analyze(ctx_for("EX-320-A")).signal("cooling_system")
    belief = belief_from(signal)
    d = decide(belief, signal, fb("correct", rationale="It's just the heat this week.",
                                  proposed_hypothesis="environmental"), [], NO_HISTORY)
    assert d.action == Action.hold
    assert d.weight.evidence_against > 0.9
    assert d.weight.weight < 0.05                       # 0.6 * 0.5 * (1 - 0.98)
    assert d.combined["degradation"] - d.combined["environmental"] > 0.5


def test_a_even_a_trusted_technician_cannot_override_strong_data(ctx_for):
    signal = analyze(ctx_for("EX-320-A")).signal("cooling_system")
    d = decide(belief_from(signal), signal,
               fb("reject", role="technician", reliability=0.95, rationale="Machine is fine."), [], NO_HISTORY)
    assert d.action == Action.hold


def test_a_repeated_pushback_still_does_not_flip(ctx_for):
    signal = analyze(ctx_for("EX-320-A")).signal("cooling_system")
    belief = belief_from(signal)
    prior = [PriorFeedback("environmental", "degradation", 0.02)] * 10   # ten earlier objections
    d = decide(belief, signal, fb("correct", rationale="Heat.", proposed_hypothesis="environmental"),
               prior, NO_HISTORY)
    assert d.action == Action.hold


def test_a_feedback_wins_when_evidence_is_genuinely_weak():
    """Feedback isn't ignored: on ambiguous data a credible correction does revise."""
    weak = Signal("cooling_system", [
        Finding(key="threshold:x", kind="threshold", subsystem="cooling_system", description="one warm shift",
                strength=0.3, effects={"degradation": 1.0, "sensor_fault": 0.6}),
    ], safety=False)
    belief = Belief("degradation", "engine_health", "cooling_system", False, 0.4)
    d = decide(belief, weak, fb("correct", role="technician", reliability=0.9,
                                rationale="Sensor reads 10 C high, verified with IR gun.",
                                proposed_hypothesis="sensor_fault"), [], NO_HISTORY)
    assert d.action == Action.revise
    assert d.alternative == "sensor_fault"


def test_rationale_is_required_for_reject_and_correct(ctx_for):
    signal = analyze(ctx_for("EX-320-A")).signal("cooling_system")
    with pytest.raises(FeedbackRejected):
        decide(belief_from(signal), signal, fb("reject"), [], NO_HISTORY)
    with pytest.raises(FeedbackRejected):
        decide(belief_from(signal), signal, fb("correct", rationale="  ", proposed_hypothesis="environmental"),
               [], NO_HISTORY)


# --------------------------------------------------------------------------- #
# (b) new verifiable evidence
# --------------------------------------------------------------------------- #

def test_b_repair_record_changes_the_belief(ctx_for):
    before = analyze(ctx_for("WL-966-B")).signal("cooling_system")
    belief = belief_from(before)
    assert belief.hypothesis == "degradation"

    # The same opinion without the record holds...
    opinion = fb("correct", role="technician", rationale="Radiator was replaced yesterday.",
                 proposed_hypothesis="resolved")
    assert decide(belief, before, opinion, [], NO_HISTORY).action == Action.hold

    # ...with the record and post-repair readings ingested, the data itself has moved.
    after = analyze(ctx_for("WL-966-B", include_holdout=True)).signal("cooling_system")
    opinion.has_new_evidence = True
    d = decide(belief, after, opinion, [], NO_HISTORY)
    assert d.action == Action.revise
    assert d.alternative == "resolved"


def test_b_claiming_a_repair_without_a_record_asks_for_it(ctx_for):
    signal = analyze(ctx_for("WL-966-B")).signal("cooling_system")
    f = fb("correct", role="technician", rationale="Radiator was replaced yesterday.",
           proposed_hypothesis="resolved")
    d = decide(belief_from(signal), signal, f, [], NO_HISTORY)
    reply = template_reply(d, f, [], title="Radiator degradation")
    assert "attach the maintenance record" in reply.message
    assert reply.data_requests[0].startswith("the maintenance record")


# --------------------------------------------------------------------------- #
# (c) safety escalates, never suppressed
# --------------------------------------------------------------------------- #

def test_c_safety_warning_escalates_instead_of_being_withdrawn(ctx_for):
    signal = analyze(ctx_for("DZ-D6-A")).signal("hydraulics")
    belief = belief_from(signal, category="safety")
    assert belief.is_safety_critical
    d = decide(belief, signal, fb("reject", rationale="Steering always feels like that, it's fine."),
               [], NO_HISTORY)
    assert d.action == Action.escalate


def test_c_safety_escalates_even_when_the_math_would_revise():
    weak = Signal("hydraulics", [
        Finding(key="note:1:hydraulics", kind="note", subsystem="hydraulics", description="steering heavy",
                strength=0.3, effects={"degradation": 1.0}, safety=True),
    ], safety=True)
    belief = Belief("degradation", "safety", "hydraulics", True, 0.35)
    d = decide(belief, weak, fb("correct", role="fleet_manager", reliability=1.0,
                                rationale="Operator error, nothing wrong.",
                                proposed_hypothesis="operating_practice"), [], NO_HISTORY)
    assert d.combined["operating_practice"] - d.combined["degradation"] > d.margin  # math alone would flip
    assert d.action == Action.escalate


def test_c_raising_an_item_to_safety_is_accepted(ctx_for):
    signal = analyze(ctx_for("EX-336-B")).signal("hydraulics")
    d = decide(belief_from(signal, category="hydraulics"), signal,
               fb("correct", role="operator", rationale="Boom drifting down with load raised.",
                  proposed_category="safety"), [], NO_HISTORY)
    assert d.action == Action.revise


def test_c_escalation_reply_says_warning_stays_active(ctx_for):
    signal = analyze(ctx_for("DZ-D6-A")).signal("hydraulics")
    f = fb("reject", rationale="It's fine.")
    d = decide(belief_from(signal, category="safety"), signal, f, [], NO_HISTORY)
    reply = template_reply(d, f, [EvidenceRef(label="E7", description="Fault 1762-1", direction="supports",
                                              strength=0.95)], title="Hydraulic leak")
    assert "warning stays active" in reply.message and "[E7]" in reply.message


# --------------------------------------------------------------------------- #
# confirm, reliability, replies
# --------------------------------------------------------------------------- #

def test_confirm_marks_confirmed_when_evidence_agrees(ctx_for):
    signal = analyze(ctx_for("EX-336-B")).signal("hydraulics")
    d = decide(belief_from(signal, category="hydraulics"), signal, fb("confirm", role="technician"), [], NO_HISTORY)
    assert d.action == Action.confirm


def test_reliability_moves_with_later_data():
    # Said "hot weather"; later data still says degradation -> refuted.
    v = judge(endorses="environmental", rejects="degradation", original_hypothesis="degradation",
              new_hypothesis="degradation", new_evidence_score=0.95)
    assert v == DataVerdict.refuted
    assert updated_reliability(0.5, v) == pytest.approx(0.4)
    # Confirmed the leak; later a repair resolved it -> the problem was real -> confirmed.
    v = judge(endorses="degradation", rejects=None, original_hypothesis="degradation",
              new_hypothesis="resolved", new_evidence_score=0.85)
    assert v == DataVerdict.confirmed
    assert updated_reliability(0.5, v) == pytest.approx(0.6)
    # Indecisive data judges nothing.
    assert judge(endorses="environmental", rejects=None, original_hypothesis="degradation",
                 new_hypothesis="degradation", new_evidence_score=0.4) is None


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def parse(self, **kw):
        self.calls.append(kw)
        return self.reply


def test_llm_reply_with_invented_citation_falls_back_to_template(ctx_for):
    signal = analyze(ctx_for("EX-320-A")).signal("cooling_system")
    f = fb("correct", rationale="Heat.", proposed_hypothesis="environmental")
    d = decide(belief_from(signal), signal, f, [], NO_HISTORY)
    refs = [EvidenceRef(label="E1", description="Coolant beyond limit", direction="supports", strength=1.0)]

    good = HoldReply(message="Holding: see [E1].", cited_evidence=["E1"], data_requests=["cool-shift readings"])
    llm = FakeLLM(good)
    assert write_reply(d, f, refs, title="t", interpretation="i", llm=llm) == good
    assert llm.calls[0]["tier"] == "reasoning"

    bad = HoldReply(message="Holding: see [E99].", cited_evidence=["E99"], data_requests=[])
    out = write_reply(d, f, refs, title="t", interpretation="i", llm=FakeLLM(bad))
    assert "[E99]" not in out.message and "contested" in out.message


def test_saying_resolved_is_vindicated_by_a_resolved_finding():
    v = judge(endorses="resolved", rejects="degradation", original_hypothesis="degradation",
              new_hypothesis="resolved", new_evidence_score=0.8)
    assert v == DataVerdict.confirmed
    # Denying the problem outright ("nothing was wrong") is still refuted by the repair.
    v = judge(endorses=None, rejects="degradation", original_hypothesis="degradation",
              new_hypothesis="resolved", new_evidence_score=0.8)
    assert v == DataVerdict.refuted
