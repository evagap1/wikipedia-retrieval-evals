# wiki-eval

Claude + Wikipedia question-answering CLI, with an eval suite that drove prompt iteration on factual accuracy, citation quality, refusal calibration, and false-premise handling.

- **Agent model:** `claude-sonnet-4-6` with two tools (`search_wikipedia`, `fetch_wikipedia_article`) backed by the live MediaWiki API
- **Default prompt:** `v3` — the best of six versions across 102 dev + heldout cases (see [Eval results](#eval-results))
- **Judge model:** `claude-opus-4-7`
- **No hosted retrieval, no vector store, no scraping** — just the official MediaWiki API

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full writeup.

## Setup

Requires Python 3.10+ and an Anthropic API key.

```bash
git clone <this-repo>
cd wikipedia-retrieval-evals

python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env
# put your ANTHROPIC_API_KEY into .env
```

That's it — `pip install -e .` registers the `wiki-eval` console script.

## Ask a question

```bash
wiki-eval ask "Who designed the Eiffel Tower, and in what year was it completed?"
```

You'll see the tool trace (which articles the agent fetched), token + latency stats, and a cited answer. Add `--json` to emit the full run record instead.

```bash
# Try a different prompt version (v1–v6 available)
wiki-eval ask "..." --prompt v6

# Curated demo set
wiki-eval --demo
```

`python -m wiki_eval ask "..."` works the same if you'd rather not use the script entry point.

## Eval results

Six prompt versions × five datasets, scored with a 0–2 rubric (Opus 4.7 judge). Numbers are AGENT pass-rate (%):

| Version | test_cases (dev) | movies (dev) | bridges (dev) | songs (HO) | rail (HO) |  avg |
|---------|-----------------:|-------------:|--------------:|-----------:|----------:|-----:|
| v1      |             87.5 |         63.6 |          65.0 |       80.0 |      75.0 | 74.2 |
| v2      |             92.5 |         72.7 |          70.0 |       85.0 |      55.0 | 75.0 |
| **v3**  |         **92.5** |     **90.9** |      **70.0** |   **90.0** |  **70.0** | **82.7** |
| v4      |             97.5 |         72.7 |          60.0 |          – |         – | 76.7 |
| v5      |             97.5 |         63.6 |          65.0 |          – |         – | 75.4 |
| v6      |             92.5 |         81.8 |          65.0 |       95.0 |      65.0 | 79.9 |

`HO` = post-hoc heldout, never used to author or tune any prompt. v3 is the CLI default.

Each case is also answered closed-book (Track A — same model, no tools) and by Opus reading the gold articles (Track B — oracle reference). The AGENT − Track A gap *is* the value of retrieval.

## Running the eval suite

```bash
# Original 40 dev cases — broad post-Sept-2025 facts
python -m eval.run_eval

# 22 movies — multi-actor identity + semantic plot, with tie-breakers
python -m eval.run_eval --cases-file eval/test_cases_movies.jsonl

# 20 bridges completed in 2026 — multi-constraint + tie-breaks + false premises
python -m eval.run_eval --cases-file eval/test_cases_bridges.jsonl

# Heldout (20 + 20)
python -m eval.run_eval --cases-file eval/heldout/test_cases_songs.jsonl
python -m eval.run_eval --cases-file eval/heldout/test_cases_railway_lines.jsonl

# A different prompt version
python -m eval.run_eval --prompt v3 --cases-file eval/test_cases_bridges.jsonl
```

Reports land in `eval/reports/<UTC stamp>_<dataset>_<prompt>.{json,md}` plus `latest.{json,md}`. Track A and Track B answers + closed-book judge results are cached per case, so prompt iteration only re-runs the agent + agent-judge.

## Layout

```
src/wiki_eval/      # agent loop, tools, prompts (v1–v6), CLI
eval/               # test cases, runner, judge, discovery scripts, reports
eval/heldout/       # heldout test cases (songs, railway lines)
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
- Max agent turns: 15 (`wiki_eval.agent.DEFAULT_MAX_TURNS`)
- Default fetch size: 4000 chars, max 12000 (`wiki_eval.tools`)
- HTTP timeout: 120s, max retries: 4 (set on both Anthropic clients)

`ANTHROPIC_API_KEY` is the only required env var.
