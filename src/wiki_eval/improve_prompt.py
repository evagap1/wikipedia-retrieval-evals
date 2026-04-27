"""Iteratively improve the system prompt without access to eval data.

A meta-agent that reads `src/wiki_eval/prompts/v<base>.md`, runs N rounds of
critique + targeted revision using Claude Opus 4.7, and writes new versioned
prompt files (`v<base+1>.md`, ..., `v<base+N>.md`).

The improver never reads `eval/test_cases*.jsonl` or `eval/reports/*`. Its only
inputs are:
- The current prompt text.
- The agent's tool schemas (so it doesn't propose tools the agent doesn't have).
- The judge's failure-mode taxonomy (so it knows what kinds of failures matter,
  without seeing which ones currently happen).

After running, evaluate with `python -m eval.run_eval --prompt v<N>` to confirm
each iteration is actually an improvement. The improver is best-effort: it can
oscillate or hit diminishing returns after 2-3 rounds.

Run:

    python -m wiki_eval.improve_prompt                       # base=v2, rounds=3
    python -m wiki_eval.improve_prompt --base v3 --rounds 2  # continue from v3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

PROMPTS_DIR = Path(__file__).parent / "prompts"
MODEL = "claude-opus-4-7"


# Failure-mode taxonomy borrowed from the judge — gives the critic a vocabulary
# for what KIND of failures matter, without revealing which ones currently
# happen on the eval.
FAILURE_MODES = (
    "hallucinated, missing_citation, wrong_citation, missed_refusal, "
    "false_premise_accepted, incomplete, wrong_fact, over_refused, padded, no_search"
)


CRITIC_SYSTEM = f"""You are a senior prompt engineer reviewing the system prompt for a Wikipedia-grounded Q&A agent built on Claude.

The agent has exactly two tools and no others:
- `search_wikipedia(query, limit)` — keyword search, returns titles + snippets.
- `fetch_wikipedia_article(title, max_chars)` — plaintext article body, default 4000 chars, max 12000.

The judge grades each answer on five dimensions (accuracy, faithfulness, citations, refusal_calibration, premise_handling) and tags failures from this taxonomy: {FAILURE_MODES}.

Your job each round: identify the SINGLE most impactful weakness in the current prompt and propose ONE targeted fix. Resist the urge to bundle multiple changes — single-change iterations are easier to validate and less likely to regress.

Constraints the prompt must keep:
- The two tools above; do not propose new tools.
- Output format: prose answer with inline `[Article Title]` citations after factual claims, ending with a `Sources:` line listing fetched titles.
- The "ground every factual claim in Wikipedia, do not answer from memory" rule.
- Roughly the current length budget (under ~80 lines).

Look for:
- Internal conflicts between rules (e.g., "stop when you have the answer" vs "verify every candidate before picking a winner on tie-breakers").
- Missing edge cases: multi-hop questions, infobox-only facts, ambiguous entity names, redirects, stub articles, future-dated events.
- Vague rules the model can't act on ("be careful", "use judgment") — replace with specific procedures.
- Examples that could be over- or under-generalized.
- Structural issues: related rules in different sections, important rules buried at the bottom.
- Underused tool affordances: when to escalate `max_chars`, when to do a follow-up search with a different angle.

Return ONLY a JSON object — no prose before/after, no markdown fences:

{{
  "weakness": "single most impactful issue, one sentence",
  "rationale": "why this matters for retrieval-grounded QA, 2-3 sentences referencing failure-mode tags from the taxonomy",
  "fix": "specific change to make, including approximate text or location, 1-3 sentences",
  "expected_failure_mode_impact": ["tag1", "tag2"]
}}"""


REVISE_SYSTEM = """You are a prompt engineer. Apply EXACTLY the requested fix to the system prompt below. Do not make additional changes — preserve every other rule, example, and section heading verbatim. Do not reorganize sections that aren't part of the fix.

Output ONLY the revised prompt. No preamble, no postscript, no markdown fences around the whole thing. Just the prompt text, ready to drop into a `.md` file."""


def _join_text(content) -> str:
    return "\n\n".join(b.text for b in content if getattr(b, "type", None) == "text").strip()


def _parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    if not cleaned.startswith("{"):
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            cleaned = m.group(0)
    return json.loads(cleaned)


def critique(client: anthropic.Anthropic, prompt_text: str) -> dict:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=CRITIC_SYSTEM,
        messages=[{"role": "user", "content": f"Current prompt:\n\n{prompt_text}"}],
    )
    raw = _join_text(resp.content)
    try:
        return _parse_json(raw)
    except json.JSONDecodeError:
        return {
            "weakness": "(parse error from critic)",
            "rationale": raw[:300],
            "fix": "(no fix produced)",
            "expected_failure_mode_impact": [],
        }


def revise(client: anthropic.Anthropic, prompt_text: str, c: dict) -> str:
    user_msg = (
        f"Critique to apply:\n\n"
        f"- weakness: {c['weakness']}\n"
        f"- rationale: {c['rationale']}\n"
        f"- fix: {c['fix']}\n\n"
        f"Current prompt to revise:\n\n{prompt_text}"
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=REVISE_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = _join_text(resp.content)
    # Strip leading/trailing fences if the model added them despite instructions
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip() + "\n"


def _next_version(base: str) -> str:
    m = re.match(r"v(\d+)$", base)
    if not m:
        raise ValueError(f"Base must be like 'v2', got {base!r}")
    return f"v{int(m.group(1)) + 1}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--base", default="v2", help="Base prompt version (default: v2).")
    parser.add_argument("--rounds", type=int, default=3, help="Number of refinement rounds (default: 3).")
    parser.add_argument("--dry-run", action="store_true", help="Print critiques but don't write new prompt files.")
    args = parser.parse_args(argv)

    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.stderr.write("ANTHROPIC_API_KEY is not set.\n")
        return 2

    base_path = PROMPTS_DIR / f"{args.base}.md"
    if not base_path.exists():
        sys.stderr.write(f"Prompt not found: {base_path}\n")
        return 2

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    current_version = args.base
    current_text = base_path.read_text()

    print(f"Improving from {current_version}.md ({len(current_text)} chars)  rounds={args.rounds}  dry_run={args.dry_run}")

    for i in range(args.rounds):
        next_version = _next_version(current_version)
        print(f"\n=== Round {i + 1}/{args.rounds}  {current_version} → {next_version} ===")

        c = critique(client, current_text)
        print(f"  weakness: {c.get('weakness')}")
        print(f"  rationale: {c.get('rationale')}")
        print(f"  fix: {c.get('fix')}")
        print(f"  expected impact: {c.get('expected_failure_mode_impact')}")

        if args.dry_run:
            print("  [dry-run] not writing")
            continue

        revised = revise(client, current_text, c)
        out_path = PROMPTS_DIR / f"{next_version}.md"
        out_path.write_text(revised)
        size_delta = len(revised) - len(current_text)
        print(f"  wrote {out_path}  ({len(revised)} chars, Δ{size_delta:+d})")

        current_version = next_version
        current_text = revised

    print(f"\nFinal: {current_version}.md")
    print(f"Verify with: python -m eval.run_eval --prompt {current_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
