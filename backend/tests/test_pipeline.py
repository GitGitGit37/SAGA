"""Inference pipeline tests. Claude is replaced by a fake that returns canned proposals."""

from types import SimpleNamespace

import pytest

from app.evidence.base_rates import beta_mean
from app.evidence.engine import analyze
from app.inference.pipeline import build_drafts
from app.inference.proposer import ClaudeProposer, RuleProposer, build_prompt
from app.inference.schemas import Claim, Proposal, ProposalSet
from app.llm import client as llm_client
from app.llm.client import ClaudeLLM, LLMError

NO_HISTORY = lambda category, hypothesis: beta_mean(0, 0)  # noqa: E731


class FakeLLM:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def _ids(ctx, frame, n=3, **where):
    df = getattr(ctx, frame)
    for k, v in where.items():
        df = df[df[k] == v]
    return [int(i) for i in df["id"].tail(n)]


def cooling_proposal(ctx, *, hypothesis="degradation", confidence=0.85, claims=None, category="engine_health"):
    hot = ctx.window_telematics.nlargest(3, "coolant_temp_max_c")
    return Proposal(
        subsystem="cooling_system", category=category, hypothesis=hypothesis,
        title="Radiator blockage developing", interpretation="Coolant temperature has drifted up.",
        recommended_action="Inspect and clean the radiator core.", confidence=confidence,
        claims=claims if claims is not None else [
            Claim(type="threshold_exceeded", statement="Coolant max above 98 C",
                  metric="coolant_temp_max_c", observation_ids=[int(i) for i in hot["id"]]),
            Claim(type="trend", statement="Coolant rising vs baseline", metric="coolant_temp_max_c"),
            Claim(type="fault_code", statement="110-16 logged", code="110-16",
                  observation_ids=_ids(ctx, "faults", 1, code="110-16")),
            Claim(type="peer_comparison", statement="Peers normal", metric="coolant_temp_max_c"),
        ])


def run(ctx, proposals):
    analysis = analyze(ctx)
    return build_drafts(analysis, ClaudeProposer(FakeLLM(ProposalSet(proposals=proposals))), NO_HISTORY)


def by_subsystem(drafts):
    return {d.subsystem: d for d in drafts}


def test_honest_claims_are_all_verified(ctx_for):
    ctx = ctx_for("EX-320-A")
    d = by_subsystem(run(ctx, [cooling_proposal(ctx)]))["cooling_system"]
    claims = [e for e in d.evidence if e.kind == "llm_claim"]
    assert len(claims) == 4 and all(c.verified for c in claims)
    assert d.confidence.unverified_claims == 0
    assert d.confidence.llm_confidence_verified == pytest.approx(0.85)
    assert d.proposer == "claude"


def test_hallucinated_claims_are_dropped_and_cost_confidence(ctx_for):
    ctx = ctx_for("EX-320-A")
    honest = by_subsystem(run(ctx, [cooling_proposal(ctx)]))["cooling_system"]
    bogus = cooling_proposal(ctx).claims + [
        Claim(type="note", statement="Technician saw a cracked radiator", observation_ids=[999999]),
        Claim(type="fault_code", statement="Fault 100-1 logged", code="100-1"),
        Claim(type="trend", statement="Oil pressure falling", metric="oil_pressure_kpa"),
    ]
    d = by_subsystem(run(ctx, [cooling_proposal(ctx, claims=bogus)]))["cooling_system"]

    unverified = [e for e in d.evidence if e.kind == "llm_claim" and not e.verified]
    assert len(unverified) == 3
    assert "don't exist" in unverified[0].details["verification"]
    assert d.confidence.llm_confidence_verified == pytest.approx(0.85 - 3 * 0.1)
    assert d.confidence.final_confidence < honest.confidence.final_confidence
    # Claims never feed evidence_score: it comes from the engine alone.
    assert d.confidence.evidence_score == honest.confidence.evidence_score


def test_nothing_verified_means_zero_llm_confidence(ctx_for):
    ctx = ctx_for("EX-320-A")
    claims = [Claim(type="repair", statement="Radiator replaced last week")]
    d = by_subsystem(run(ctx, [cooling_proposal(ctx, confidence=0.99, claims=claims)]))["cooling_system"]
    assert d.confidence.llm_confidence_verified == 0.0


def test_final_confidence_is_the_configured_blend(ctx_for):
    ctx = ctx_for("EX-320-A")
    c = by_subsystem(run(ctx, [cooling_proposal(ctx)]))["cooling_system"].confidence
    w = c.weights
    assert w == {"evidence": 0.5, "llm": 0.2, "history": 0.3}
    assert c.final_confidence == pytest.approx(
        w["evidence"] * c.evidence_score + w["llm"] * c.llm_confidence_verified + w["history"] * c.base_rate,
        abs=1e-3)


def test_engine_overrules_a_wrong_llm_hypothesis(ctx_for):
    """Claude says 'it's the weather'; ambient and peers say otherwise, so the engine's reading wins."""
    ctx = ctx_for("EX-320-A")
    wrong = cooling_proposal(ctx, hypothesis="environmental", category="environmental_condition", confidence=0.9)
    d = by_subsystem(run(ctx, [wrong]))["cooling_system"]
    assert d.hypothesis == "degradation"
    assert d.proposer == "engine_override"
    assert d.alternatives and d.alternatives[0]["hypothesis"] == "environmental"


def test_safety_flag_comes_from_the_engine_not_the_llm(ctx_for):
    ctx = ctx_for("DZ-D6-A")
    low = ctx.window_telematics.nsmallest(3, "hydraulic_pressure_bar")
    p = Proposal(subsystem="hydraulics", category="hydraulics", hypothesis="degradation",
                 title="Hydraulic leak", interpretation="Pressure is dropping.", recommended_action="Fix leak.",
                 confidence=0.8, claims=[Claim(type="threshold_exceeded", statement="Low pressure",
                                               metric="hydraulic_pressure_bar",
                                               observation_ids=[int(i) for i in low["id"]])])
    d = by_subsystem(run(ctx, [p]))["hydraulics"]
    assert d.category == "hydraulics"   # LLM didn't call it safety...
    assert d.is_safety_critical         # ...but the engine did (fault 1762-1, steering note)


def test_proposals_without_an_engine_signal_are_dropped(ctx_for):
    ctx = ctx_for("EX-320-A")
    stray = Proposal(subsystem="electrical", category="electrical", hypothesis="degradation",
                     title="Battery failing", interpretation="?", recommended_action="?", confidence=0.9, claims=[])
    drafts = by_subsystem(run(ctx, [stray]))
    assert "electrical" not in drafts
    assert drafts["cooling_system"].proposer == "rules"  # signal the LLM skipped still gets covered


def test_rules_proposer_drops_the_llm_term(ctx_for):
    ctx = ctx_for("WL-950-A")
    drafts = by_subsystem(build_drafts(analyze(ctx), RuleProposer(), NO_HISTORY))
    c = drafts["operator"].confidence
    assert drafts["operator"].hypothesis == "operating_practice"
    assert c.weights["llm"] == 0 and c.llm_confidence_verified == 0
    assert c.final_confidence == pytest.approx(0.625 * c.evidence_score + 0.375 * c.base_rate, abs=1e-3)


def test_llm_failure_falls_back_to_rules(ctx_for):
    ctx = ctx_for("EX-336-B")
    proposer = ClaudeProposer(FakeLLM(error=LLMError("rate limited")))
    drafts = build_drafts(analyze(ctx), proposer, NO_HISTORY)
    assert [d.proposer for d in drafts] == ["rules"]
    assert drafts[0].hypothesis == "degradation"


def test_repair_produces_resolved_inference(ctx_for):
    ctx = ctx_for("WL-966-B", include_holdout=True)
    d = by_subsystem(build_drafts(analyze(ctx), RuleProposer(), NO_HISTORY))["cooling_system"]
    assert d.hypothesis == "resolved"
    assert any(e.kind == "maintenance" and e.direction == "supports" for e in d.evidence)


def test_prompt_contains_signals_and_citable_ids(ctx_for):
    ctx = ctx_for("EX-320-A")
    prompt = build_prompt(analyze(ctx))
    assert "## cooling_system" in prompt and "[peers:coolant_temp_max_c]" in prompt
    assert str(int(ctx.window_telematics["id"].iloc[-1])) in prompt
    assert "110-16" in prompt


def test_claude_client_uses_structured_output_and_handles_refusal(monkeypatch):
    calls = []

    def fake_parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason=fake_parse.stop_reason, stop_details=None,
                               parsed_output=ProposalSet(proposals=[]),
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    settings = SimpleNamespace(anthropic_api_key="test", claude_fast_model="claude-sonnet-5-5",
                               claude_reasoning_model="claude-opus-5-5")
    llm = ClaudeLLM(settings)
    monkeypatch.setattr(llm.client.beta.messages, "parse", fake_parse)

    fake_parse.stop_reason = "end_turn"
    out = llm.parse(tier="fast", system="s", prompt="p", schema=ProposalSet)
    assert out == ProposalSet(proposals=[])
    assert calls[0]["model"] == "claude-sonnet-5-5" and calls[0]["output_format"] is ProposalSet
    assert calls[0]["fallbacks"] == "default"

    fake_parse.stop_reason = "refusal"
    with pytest.raises(LLMError):
        llm.parse(tier="reasoning", system="s", prompt="p", schema=ProposalSet)
    assert calls[1]["model"] == "claude-opus-5-5"


def test_gemini_client_validates_json_against_schema(monkeypatch):
    calls = []

    def fake_generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=fake_generate.text, usage_metadata=None,
                               candidates=[SimpleNamespace(finish_reason="STOP")])

    settings = SimpleNamespace(gemini_api_key="test", gemini_fast_model="gemini-fast",
                               gemini_reasoning_model="gemini-reasoning")
    llm = llm_client.GeminiLLM(settings)
    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate)

    fake_generate.text = '{"proposals": []}'
    assert llm.parse(tier="fast", system="s", prompt="p", schema=ProposalSet) == ProposalSet(proposals=[])
    assert calls[0]["model"] == "gemini-fast"
    assert calls[0]["config"].response_json_schema == ProposalSet.model_json_schema()

    fake_generate.text = '{"wrong": 1}'
    with pytest.raises(LLMError):
        llm.parse(tier="reasoning", system="s", prompt="p", schema=ProposalSet)
    assert calls[1]["model"] == "gemini-reasoning"


def test_get_llm_picks_claude_then_gemini(monkeypatch):
    both = SimpleNamespace(anthropic_api_key="a", gemini_api_key="g", claude_fast_model="c", claude_reasoning_model="c")
    monkeypatch.setattr(llm_client, "get_settings", lambda: both)
    assert llm_client.get_llm().name == "claude"
    gemini_only = SimpleNamespace(anthropic_api_key=None, gemini_api_key="g")
    monkeypatch.setattr(llm_client, "get_settings", lambda: gemini_only)
    assert llm_client.get_llm().name == "gemini"


def test_get_llm_without_key_returns_none(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings",
                        lambda: SimpleNamespace(anthropic_api_key=None, gemini_api_key=None))
    assert llm_client.get_llm() is None
