"""Answer questions ONLY from retrieved memories, citing them by id.

If the memories don't cover the question, say so. Citations are checked: an answer that
cites a memory it wasn't given is rejected and replaced with a deterministic answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.llm.client import LLMError, StructuredLLM
from app.recall.retriever import Filters, Memory

log = logging.getLogger(__name__)
CITATION = re.compile(r"\[([IO]\d+)\]")
MIN_RELEVANCE = 0.68  # bge-small cosine similarity: on-topic >= ~0.69, unrelated <= ~0.65


class RecallAnswer(BaseModel):
    answer: str = Field(description="Answer in plain language, citing memories inline as [I12] or [O45].")
    cited: list[str] = Field(description="Ids of every memory cited, e.g. ['I12', 'O45'].")
    sufficient: bool = Field(description="False if the memories don't contain enough to answer.")


@dataclass
class Answer:
    text: str
    cited: list[str]
    sufficient: bool
    generated_by: str  # claude | gemini | template


SYSTEM = """You are the recall interface of Ride Along, a memory layer for heavy equipment. \
Answer the user's question using ONLY the memories provided. Memories are inferences the \
system has made (ids starting with I) and operator/technician notes (ids starting with O).

Rules:
- Cite every fact with the memory id in square brackets, e.g. [I12] or [O45].
- Do not use outside knowledge about the machines or invent readings, dates or causes.
- Mention status where it matters: contested items are disputed, escalated items are \
safety-critical and awaiting human review, superseded items are older beliefs.
- If the memories don't answer the question, set sufficient=false and say what is missing \
instead of guessing. Partial answers are fine if you say which part is unknown.
- Keep it short: 2-6 sentences, or a few bullets."""


def _scope(filters: Filters) -> str:
    parts = [", ".join(filters.asset_tags) or None,
             filters.subsystem.replace("_", " ") if filters.subsystem else None,
             "safety-critical items" if filters.safety_only else None]
    return " ".join(p for p in parts if p)


def _line(m: Memory) -> str:
    label = m.meta.get("title") or m.content[:160]
    extra = [m.meta["status"]] if "status" in m.meta else []
    if m.meta.get("escalated"):
        extra.append("escalated")
    if "final_confidence" in m.meta:
        extra.append(f"{m.meta['final_confidence']:.0%}")
    return f"- [{m.ref}] {m.asset_tag}: {label}" + (f" ({', '.join(extra)})" if extra else "")


def template_answer(question: str, memories: list[Memory], filters: Filters | None = None) -> Answer:
    """Deterministic answer used without Claude. Lists the best memories, and says plainly
    when none of them is actually about what was asked."""
    filters = filters or Filters()
    scope = _scope(filters)
    if not memories:
        return Answer(f"I don't have any memories{f' for {scope}' if scope else ''} that match that question.",
                      [], False, "template")

    on_topic = [m for m in memories if m.similarity >= MIN_RELEVANCE]
    inferences = [m for m in on_topic if m.owner_type == "inference"
                  and (not filters.subsystem or m.meta.get("subsystem") == filters.subsystem)]
    if filters.safety_only:
        inferences = [m for m in memories if m.owner_type == "inference"]  # already filtered to safety

    if not inferences and (filters.subsystem or not on_topic):
        lines = [f"I have no inferences{f' about {scope}' if scope else ''} that answer this."]
        if on_topic:
            lines.append("The closest records are:")
            lines += [f"- [{m.ref}] {m.asset_tag}: {m.content[:160]}" for m in on_topic[:3]]
        return Answer("\n".join(lines), [m.ref for m in on_topic[:3]], False, "template")

    chosen = (inferences + [m for m in on_topic if m not in inferences])[:5]
    return Answer("Here is what memory holds:\n" + "\n".join(_line(m) for m in chosen),
                  [m.ref for m in chosen], True, "template")


def answer_question(question: str, memories: list[Memory], llm: StructuredLLM | None,
                    filters: Filters | None = None) -> Answer:
    if not memories or llm is None:
        return template_answer(question, memories, filters)

    allowed = {m.ref for m in memories}
    prompt = "QUESTION: " + question + "\n\nMEMORIES:\n" + "\n".join(
        f"[{m.ref}] ({m.owner_type}, {m.asset_tag}, {m.when:%Y-%m-%d}) {m.content}" if m.when
        else f"[{m.ref}] ({m.owner_type}, {m.asset_tag}) {m.content}" for m in memories)
    try:
        r = llm.parse(tier="reasoning", system=SYSTEM, prompt=prompt, schema=RecallAnswer,
                      effort="low", max_tokens=4000)
    except LLMError as exc:
        log.warning("recall answer failed (%s); using template", exc)
        return template_answer(question, memories, filters)

    cited = set(r.cited) | set(CITATION.findall(r.answer))
    if not cited <= allowed:
        log.warning("answer cited memories it wasn't given: %s", sorted(cited - allowed))
        return template_answer(question, memories, filters)
    if r.sufficient and not cited:
        log.warning("answer claimed sufficiency without citations")
        return template_answer(question, memories, filters)
    return Answer(r.answer, sorted(cited), r.sufficient, getattr(llm, "name", "claude"))
