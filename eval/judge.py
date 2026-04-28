"""LLM-as-judge using Claude Opus 4.7.

The judge grades each agent answer along five dimensions, returning structured
JSON. We bias the rubric toward Wikipedia-faithfulness rather than absolute
truth — the system's goal is to reflect what Wikipedia says, not to have its
own opinions.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic

JUDGE_MODEL = "claude-opus-4-7"
CLOSED_BOOK_MODEL = "claude-sonnet-4-6"
REFERENCE_MODEL = "claude-opus-4-7"


# ---------------------------------------------------------------------------
# Track A — closed-book baseline (the agent model with no tools)
# ---------------------------------------------------------------------------


CLOSED_BOOK_SYSTEM = (
    "You are answering from your own parametric knowledge ONLY. You have NO "
    "tools, NO Wikipedia access, NO web/internet access, NO search of any kind. "
    "Do not pretend to look anything up. If your training data does not cover "
    "the question (for example because the event happened after your training "
    "cutoff), say so explicitly — do not guess and do not fabricate. Be concise."
)


def closed_book_answer(client: anthropic.Anthropic, question: str) -> str:
    """Track A: ask the agent model the question with NO tools and NO internet.

    We pass no ``tools=`` argument, which means the Anthropic API serves the
    model purely from parametric knowledge. No hosted retrieval tool (e.g.
    web_search) is enabled. The system prompt above also forbids the model
    from pretending to look anything up. For post-cutoff questions this
    baseline should fail or refuse, making the value of retrieval visible.
    """
    resp = client.messages.create(
        model=CLOSED_BOOK_MODEL,
        max_tokens=600,
        system=CLOSED_BOOK_SYSTEM,
        messages=[{"role": "user", "content": question}],
        # Intentionally no `tools=` — closed-book means closed-book.
    )
    return _join_text(resp.content)


# ---------------------------------------------------------------------------
# Track B — open-book reference answer (Opus reads the gold articles)
# ---------------------------------------------------------------------------


REFERENCE_SYSTEM = (
    "You are writing a reference answer for an evaluation. You have been given "
    "the relevant Wikipedia article excerpts. Use ONLY information present in "
    "those excerpts. Do not draw on outside knowledge. If the excerpts do not "
    "answer the question, say so. Be concise (under 150 words). Cite article "
    "titles inline as [Article Title]."
)


def reference_answer(
    client: anthropic.Anthropic,
    question: str,
    gold_articles: list[str],
    article_texts: dict[str, str],
) -> str:
    """Track B: build the gold reference by feeding Opus the actual article text."""
    if not gold_articles:
        return "(no gold articles — refusal/non-encyclopedic question)"
    parts = [f"### {t}\n\n{article_texts.get(t, '[article not fetched]')}" for t in gold_articles]
    user_msg = (
        f"Question: {question}\n\n"
        f"Wikipedia excerpts:\n\n" + "\n\n---\n\n".join(parts)
    )
    resp = client.messages.create(
        model=REFERENCE_MODEL,
        max_tokens=600,
        system=REFERENCE_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _join_text(resp.content)


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------


JUDGE_SYSTEM = """You grade answers from a Wikipedia-grounded Q&A system.

You will receive:
- A question
- The system's answer (may include inline citations like [Article Title])
- A reference answer derived directly from the relevant Wikipedia articles
- The expected behavior (what a good answer looks like for this case)
- The list of Wikipedia article titles the system actually fetched

Score each dimension on a 0-2 integer scale:

- accuracy (0-2): Does the system's answer agree with the reference answer on the substantive facts? 0 = factually wrong on the main point. 1 = partially correct or missing a key element. 2 = fully correct.
- faithfulness (0-2): Are the system's claims supported by what Wikipedia says (i.e. by the reference answer)? 0 = clearly hallucinates or invents details. 1 = a few unsupported flourishes. 2 = every claim is faithful.
- citations (0-2): 0 = no inline citations and no Sources line. 1 = either inline OR a Sources list, but not both, or inconsistent. 2 = inline citations after factual claims AND a Sources list naming articles that were actually fetched.
- refusal_calibration (0-2): If this is a question that should be refused or hedged (e.g. real-time data, post-event details Wikipedia doesn't cover, false premise), did the system refuse / correct / hedge appropriately? 2 = perfect calibration. 1 = partially calibrated (e.g. answered but with caveats). 0 = answered confidently when it should have refused, or refused when it shouldn't.
- premise_handling (0-2): If the question contains a false premise, did the system identify and correct it? 2 = corrected explicitly. 1 = answered around it. 0 = accepted the false premise. If the question has no false premise, score 2.

Also provide:
- overall_pass: boolean. True if accuracy >= 1 AND faithfulness == 2 AND citations >= 1 AND refusal_calibration >= 1 AND premise_handling >= 1.
- failure_modes: list of short tags from {"hallucinated", "missing_citation", "wrong_citation", "missed_refusal", "false_premise_accepted", "incomplete", "wrong_fact", "over_refused", "padded", "no_search"}.
- comment: one sentence explaining the most important issue, or "OK" if the answer is solid.

Return ONLY a JSON object — no prose before or after, no markdown fences."""


@dataclass
class JudgeScore:
    accuracy: int = 0
    faithfulness: int = 0
    citations: int = 0
    refusal_calibration: int = 0
    premise_handling: int = 0
    overall_pass: bool = False
    failure_modes: list[str] = field(default_factory=list)
    comment: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "faithfulness": self.faithfulness,
            "citations": self.citations,
            "refusal_calibration": self.refusal_calibration,
            "premise_handling": self.premise_handling,
            "overall_pass": self.overall_pass,
            "failure_modes": self.failure_modes,
            "comment": self.comment,
        }


def judge_answer(
    client: anthropic.Anthropic,
    *,
    question: str,
    expected_behavior: str,
    agent_answer: str,
    reference_answer_text: str,
    fetched_titles: list[str],
) -> JudgeScore:
    user_msg = (
        f"Question:\n{question}\n\n"
        f"Expected behavior (case spec):\n{expected_behavior}\n\n"
        f"Reference answer (derived from Wikipedia gold articles):\n{reference_answer_text}\n\n"
        f"System answer:\n{agent_answer or '(empty)'}\n\n"
        f"Articles the system actually fetched: "
        f"{json.dumps(fetched_titles, ensure_ascii=False) if fetched_titles else '[]'}"
    )
    # Prompt cache the static system prompt — the judge runs ~80x per eval and
    # the system prompt is 2.5k tokens. With cache, repeated calls hit the
    # ephemeral cache and are much cheaper + faster.
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=600,
        system=[
            {
                "type": "text",
                "text": JUDGE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = _join_text(resp.content)
    return _parse_judge_output(raw)


def _parse_judge_output(raw: str) -> JudgeScore:
    """Parse JSON, tolerant of stray fencing or prose."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # strip ```json ... ``` if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    # Find the first {...} block as a fallback
    if not cleaned.startswith("{"):
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(0)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return JudgeScore(comment=f"PARSE_ERROR: {raw[:200]}", raw=raw)
    return JudgeScore(
        accuracy=int(data.get("accuracy", 0)),
        faithfulness=int(data.get("faithfulness", 0)),
        citations=int(data.get("citations", 0)),
        refusal_calibration=int(data.get("refusal_calibration", 0)),
        premise_handling=int(data.get("premise_handling", 0)),
        overall_pass=bool(data.get("overall_pass", False)),
        failure_modes=list(data.get("failure_modes", [])),
        comment=str(data.get("comment", "")),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _join_text(content: list[Any]) -> str:
    return "\n\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=4,
        timeout=120.0,
    )
