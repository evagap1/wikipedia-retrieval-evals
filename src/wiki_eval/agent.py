"""Claude agent loop with Wikipedia tools.

The agent runs a standard Anthropic tool-use loop: send the user question,
dispatch any tool calls, feed results back, repeat until the model stops
emitting ``tool_use`` blocks. The loop captures a full trace so the eval
harness can grade not just the answer but the search behavior that produced it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from wiki_eval.tools import TOOL_SCHEMAS, dispatch_tool

DEFAULT_AGENT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_TURNS = 15  # hard cap on tool-use rounds — protects against loops

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(version: str = "v1") -> str:
    """Load a versioned system prompt from src/wiki_eval/prompts/<version>.md."""
    path = PROMPTS_DIR / f"{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt version not found: {path}")
    return path.read_text()


# ---------------------------------------------------------------------------
# Trace types
# ---------------------------------------------------------------------------


@dataclass
class ToolCallTrace:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    latency_ms: int


@dataclass
class AgentRun:
    question: str
    answer: str
    prompt_version: str
    model: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    turns: int = 0
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_latency_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "turns": self.turns,
            "stop_reason": self.stop_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_latency_ms": self.total_latency_ms,
            "error": self.error,
            "tool_calls": [
                {
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "result_summary": _summarize_result(tc.result),
                    "latency_ms": tc.latency_ms,
                }
                for tc in self.tool_calls
            ],
        }


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Compact a tool result for the trace report (full text would balloon JSON)."""
    if "error" in result:
        return {"error": result["error"]}
    if "hits" in result:
        return {
            "kind": "search",
            "n_hits": len(result["hits"]),
            "titles": [h["title"] for h in result["hits"]],
        }
    if "text" in result:
        return {
            "kind": "fetch",
            "title": result.get("title"),
            "char_count": result.get("char_count"),
            "truncated": result.get("truncated"),
        }
    return result


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def run_agent(
    question: str,
    *,
    prompt_version: str = "v1",
    model: str = DEFAULT_AGENT_MODEL,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    client: anthropic.Anthropic | None = None,
) -> AgentRun:
    """Run the Wikipedia agent on a question and return a full trace."""
    system_prompt = load_prompt(prompt_version)
    client = client or anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    run = AgentRun(
        question=question,
        answer="",
        prompt_version=prompt_version,
        model=model,
    )
    t0 = time.monotonic()

    for turn in range(max_turns):
        run.turns = turn + 1
        response = None
        delay = 2.0
        for attempt in range(5):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )
                break
            except anthropic.RateLimitError:
                if attempt == 4:
                    run.error = "Rate-limited 5x; giving up"
                    break
                time.sleep(delay)
                delay *= 2
            except anthropic.APIError as e:
                run.error = f"Anthropic API error: {e}"
                break
        if response is None:
            break

        run.input_tokens += response.usage.input_tokens
        run.output_tokens += response.usage.output_tokens
        run.stop_reason = response.stop_reason or ""

        # Append the assistant message verbatim so tool_use ids round-trip.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            run.answer = _extract_final_text(response.content)
            break

        # Dispatch every tool_use block in this turn before replying.
        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            args = block.input or {}
            tc_t0 = time.monotonic()
            result = dispatch_tool(block.name, args)
            tc_latency = int((time.monotonic() - tc_t0) * 1000)
            run.tool_calls.append(
                ToolCallTrace(
                    name=block.name,
                    arguments=args,
                    result=result,
                    latency_ms=tc_latency,
                )
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})
    else:
        # Loop exhausted without a final answer.
        run.error = f"Max turns ({max_turns}) reached without a final answer"

    run.total_latency_ms = int((time.monotonic() - t0) * 1000)
    return run


def _extract_final_text(content: list[Any]) -> str:
    parts = [b.text for b in content if getattr(b, "type", None) == "text"]
    return "\n\n".join(p for p in parts if p).strip()
