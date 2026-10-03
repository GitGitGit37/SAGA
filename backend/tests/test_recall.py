from datetime import datetime, timezone

from app.recall.answerer import RecallAnswer, answer_question, template_answer
from app.recall.retriever import Filters, Memory, infer_filters

TAGS = ["EX-320-A", "EX-320-C", "EX-336-B", "WL-966-B", "DZ-D6-A"]
T = datetime(2026, 9, 29, tzinfo=timezone.utc)


def mem(ref, sim, *, owner="inference", tag="EX-320-A", **meta):
    return Memory(ref, owner, int(ref[1:]), tag, f"content of {ref}", sim, sim, T, meta)


def test_filters_inferred_from_question():
    f = infer_filters("What's been going on with excavator 320-A's hydraulics?", TAGS)
    assert f.asset_tags == ["EX-320-A"] and f.subsystem == "hydraulics"
    assert infer_filters("why is ex320a running hot", TAGS).asset_tags == ["EX-320-A"]
    assert infer_filters("Which machines have safety issues?", TAGS).safety_only
    assert infer_filters("anything contested on WL-966-B?", TAGS).status == "contested"
    # "320" alone is ambiguous between EX-320-A and EX-320-C, so no asset filter
    assert infer_filters("how are the 320s doing", TAGS).asset_tags == []


def test_explicit_filters_win():
    f = infer_filters("hydraulics?", TAGS, Filters(asset_tags=["DZ-D6-A"]))
    assert f.asset_tags == ["DZ-D6-A"]


def test_template_answers_from_relevant_inferences():
    a = template_answer("why hot", [mem("I3", 0.73, subsystem="cooling_system", title="Radiator blockage",
                                        status="active", final_confidence=0.8)],
                        Filters(asset_tags=["EX-320-A"], subsystem="cooling_system"))
    assert a.sufficient and "[I3]" in a.text and a.cited == ["I3"]


def test_template_says_so_when_memory_has_nothing_relevant():
    # Only routine notes match "hydraulics" on EX-320-A: say there's no inference about it.
    notes = [mem("O1089", 0.76, owner="observation", kind="maintenance_note")]
    a = template_answer("hydraulics?", notes, Filters(asset_tags=["EX-320-A"], subsystem="hydraulics"))
    assert not a.sufficient and a.text.startswith("I have no inferences about EX-320-A hydraulics")
    # Off-topic question: everything below the relevance bar.
    a = template_answer("tire pressure?", [mem("I4", 0.65, subsystem="hydraulics", title="x")])
    assert not a.sufficient
    assert not template_answer("anything?", []).sufficient


class FakeLLM:
    def __init__(self, result):
        self.result = result

    def parse(self, **kw):
        return self.result


def test_claude_answer_with_valid_citations_is_used():
    ms = [mem("I3", 0.73), mem("O1094", 0.72, owner="observation")]
    r = RecallAnswer(answer="Coolant drift [I3]; operator saw warnings [O1094].", cited=["I3", "O1094"],
                     sufficient=True)
    a = answer_question("why hot", ms, FakeLLM(r))
    assert a.generated_by == "claude" and a.cited == ["I3", "O1094"]


def test_claude_answer_citing_unretrieved_memory_is_rejected():
    ms = [mem("I3", 0.73, title="t", subsystem="cooling_system", status="active", final_confidence=0.8)]
    r = RecallAnswer(answer="Radiator replaced [I99].", cited=["I99"], sufficient=True)
    a = answer_question("why hot", ms, FakeLLM(r))
    assert a.generated_by == "template" and "I99" not in a.text


def test_claude_claiming_sufficiency_without_citations_is_rejected():
    ms = [mem("I3", 0.73, title="t", subsystem="cooling_system", status="active", final_confidence=0.8)]
    a = answer_question("why hot", ms, FakeLLM(RecallAnswer(answer="It's the radiator.", cited=[], sufficient=True)))
    assert a.generated_by == "template"


def test_claude_can_say_memory_is_insufficient():
    ms = [mem("I3", 0.73)]
    r = RecallAnswer(answer="Memory has nothing on tire pressure.", cited=[], sufficient=False)
    a = answer_question("tires?", ms, FakeLLM(r))
    assert a.generated_by == "claude" and not a.sufficient
