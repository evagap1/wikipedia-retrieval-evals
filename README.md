# wiki-eval

Claude + Wikipedia question-answering, with an eval suite that measures factual accuracy, faithfulness to Wikipedia, citation quality, refusal calibration, and false-premise handling.

- **Agent model:** `claude-sonnet-4-6` with two tools (`search_wikipedia`, `fetch_wikipedia_article`) backed by the live MediaWiki API
- **Judge model:** `claude-opus-4-7`
- **No hosted retrieval, no vector store, no scraping** — just the official MediaWiki API

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full writeup.

## Quick start

```bash
git clone <this-repo>
cd wikipedia-retrieval-evals

python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env
# put your ANTHROPIC_API_KEY into .env

# Single question
python -m wiki_eval ask "Who designed the Eiffel Tower, and when was it completed?"

# Demo mode (5 curated questions, full traces)
python -m wiki_eval --demo
```

## The eval suite

Three datasets, 82 cases total. Every case is answered three times — by the agent, by the same model with no tools (closed-book baseline, "Track A"), and by Opus reading the gold articles (oracle reference, "Track B") — and graded on a 0–2 rubric across five dimensions. The AGENT − Track A gap *is* the retrieval value.

```bash
# Original 40 cases — broad post-Sept-2025 facts
python -m eval.run_eval

# 22 movies — multi-actor identity + semantic plot, with tie-breakers
python -m eval.run_eval --cases-file eval/test_cases_movies.jsonl

# 20 bridges completed in 2026 — multi-constraint + tie-breaks + false premises
python -m eval.run_eval --cases-file eval/test_cases_bridges.jsonl

# Try a different prompt version
python -m eval.run_eval --prompt v2 --cases-file eval/test_cases_bridges.jsonl
```

Reports land in `eval/reports/<UTC stamp>_<dataset>_<prompt>.{json,md}` plus `latest.{json,md}`. Track A and Track B answers are cached per case, so prompt iteration only re-runs the agent + judge.

### Current baselines (prompt v1)

| Dataset | N | AGENT pass% | Track A pass% | Top failure mode |
|---|---:|---:|---:|---|
| `test_cases.jsonl` | 40 | 87.5 | 2.5 | `no_search` |
| `test_cases_movies.jsonl` | 22 | 63.6 | 0.0 | `incomplete` / `missing_citation` |
| `test_cases_bridges.jsonl` | 20 | 65.0 | 0.0 | `missing_citation` |

The two harder evals are where prompt iteration earns its keep.

## Layout

```
src/wiki_eval/      # agent loop, tools, prompts, CLI
eval/               # test cases, runner, judge, discovery scripts, reports
.claude/skills/     # workflow skills: run-eval, add-test-case, tune-prompt
docs/DESIGN.md      # design rationale (the writeup)
```

## Skills

Three project-scoped Claude Code skills cover the day-to-day workflow:

- [`.claude/skills/run-eval`](.claude/skills/run-eval/SKILL.md) — run the suite, interpret the 3-track comparison report, find regressions
- [`.claude/skills/add-test-case`](.claude/skills/add-test-case/SKILL.md) — JSONL schema, the post-cutoff rule, question-design heuristics
- [`.claude/skills/tune-prompt`](.claude/skills/tune-prompt/SKILL.md) — iterate the system prompt driven by the judge's failure-mode counts

## Models and config

- Agent + closed-book baseline: `claude-sonnet-4-6` (`wiki_eval.agent.DEFAULT_AGENT_MODEL`, `eval.judge.CLOSED_BOOK_MODEL`)
- Reference + judge: `claude-opus-4-7` (`eval.judge.REFERENCE_MODEL`, `JUDGE_MODEL`)
- Max agent turns: 10 (`wiki_eval.agent.DEFAULT_MAX_TURNS`)
- Default fetch size: 4000 chars, max 12000 (`wiki_eval.tools`)

`ANTHROPIC_API_KEY` is the only required env var.
