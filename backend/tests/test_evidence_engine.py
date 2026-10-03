import pandas as pd
import pytest

from app.db.enums import Hypothesis as H
from app.evidence.base_rates import beta_mean
from app.evidence.engine import analyze
from app.evidence.maintenance import _present, PHYSICAL
from app.evidence.types import Finding, noisy_or, score_hypothesis

EXPECTED = [
    # asset, subsystem, best hypothesis, safety
    ("EX-320-A", "cooling_system", H.degradation, False),
    ("EX-336-B", "hydraulics", H.degradation, False),
    ("DZ-D6-A", "hydraulics", H.degradation, True),
    ("WL-966-B", "cooling_system", H.degradation, False),
    ("WL-950-A", "operator", H.operating_practice, False),
    ("DZ-D8-B", "operator", H.operating_practice, False),
    ("EX-336-D", "cooling_system", H.resolved, False),
]


@pytest.mark.parametrize("tag,subsystem,hypothesis,safety", EXPECTED)
def test_scenarios_get_the_right_explanation(ctx_for, tag, subsystem, hypothesis, safety):
    a = analyze(ctx_for(tag))
    sig = a.signal(subsystem)
    assert sig in a.signals, f"{tag}: no {subsystem} signal"
    assert sig.best().hypothesis == hypothesis
    assert sig.safety is safety


@pytest.mark.parametrize("tag", ["EX-320-C", "WL-950-C", "DZ-D6-C"])
def test_healthy_controls_raise_no_signals(ctx_for, tag):
    assert analyze(ctx_for(tag)).signals == []


def test_hot_weather_explanation_is_contradicted(ctx_for):
    sig = analyze(ctx_for("EX-320-A")).signal("cooling_system")
    keys = {f.key for f in sig.findings}
    assert {"ambient:coolant_temp_max_c", "peers:coolant_temp_max_c"} <= keys
    assert sig.score(H.environmental).score < 0.2
    assert sig.score(H.degradation).score > 0.9


def test_repair_record_flips_to_resolved(ctx_for):
    before = analyze(ctx_for("WL-966-B")).signal("cooling_system")
    after = analyze(ctx_for("WL-966-B", include_holdout=True)).signal("cooling_system")
    assert before.best().hypothesis == H.degradation
    assert after.best().hypothesis == H.resolved
    assert after.score(H.degradation).score < 0.3


def test_sensor_glitch_is_not_called_degradation(ctx_for):
    # Before the connector repair (day 53 spike, repair day 54) the best story is a sensor fault.
    sig = analyze(ctx_for("EX-336-D", day=53)).signal("cooling_system")
    assert sig.best().hypothesis == H.sensor_fault
    assert sig.score(H.sensor_fault).score - sig.score(H.degradation).score > 0.5
    assert not sig.safety  # the 110-0 events coincide with the spikes


def test_drift_detected_before_it_is_severe(ctx_for):
    assert analyze(ctx_for("EX-320-A", day=30)).signals == []          # before onset (day 34)
    early = analyze(ctx_for("EX-320-A", day=46)).signal("cooling_system")
    assert early.best().hypothesis == H.degradation and early.best().score > 0.5


def test_every_finding_cites_real_observations(ctx_for):
    for tag in ["EX-320-A", "DZ-D6-A", "EX-336-D", "WL-950-A"]:
        ctx = ctx_for(tag)
        known = set(ctx.telematics["id"]) | set(ctx.faults["id"]) | set(ctx.notes["id"])
        for f in analyze(ctx).findings:
            assert set(f.observation_ids) <= known, f.key
            assert f.observation_ids or f.key.startswith(("ambient:", "peers:")), f.key


def test_negated_physical_evidence_is_ignored():
    assert _present("coolant level at full mark, no external coolant leaks", PHYSICAL) == []
    assert _present("oil film on boom cylinder rod and some drips", PHYSICAL) == ["drips", "oil film"]


def test_noisy_or_and_contradiction():
    f = lambda s, e: Finding(key="k", kind="threshold", subsystem="x", description="", strength=s, effects=e)
    assert noisy_or([0.5, 0.5]) == pytest.approx(0.75)
    score, sup, con = score_hypothesis([f(0.5, {H.degradation: 1}), f(0.5, {H.degradation: 1}),
                                        f(0.8, {H.degradation: -0.5})], H.degradation)
    assert score == pytest.approx(0.75 * 0.6)
    assert len(sup) == 2 and len(con) == 1


def test_base_rate_beta_prior():
    assert beta_mean(0, 0).value == pytest.approx(0.5)
    assert beta_mean(4, 0).value == pytest.approx((4 + 2) / 8)   # prior 0.5, strength 4
    assert beta_mean(1, 0).value < 0.65                         # one outcome can't swing it far
