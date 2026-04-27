---
name: run-eval
description: Run the wiki-eval suite, interpret the 3-track comparison report, and find regressions.
---

# run-eval

Use this when you want to grade the agent's behavior on one of the eval datasets and read the resulting report.

## TL;DR

```bash
# All three active eval files
python -m eval.run_eval                                                    # 40 cases — original (test_cases.jsonl)
python -m eval.run_eval --cases-file eval/test_cases_movies.jsonl          # 22 cases — movies (multi-actor + plot)
python -m eval.run_eval --cases-file eval/test_cases_bridges.jsonl         # 20 cases — 2026 bridges
```

Reports land in `eval/reports/<UTC stamp>_<dataset>_<prompt>.{json,md}` plus `latest.{json,md}`.

## What gets graded

Each case is answered three times:

| Track | Setup | Purpose |
|---|---|---|
| **AGENT** | Sonnet 4.6 + Wikipedia tools | the system under test |
| **Track A** | Sonnet 4.6, no tools / no internet | parametric-only baseline; quantifies retrieval gain |
| **Track B** | Opus 4.7 reading the gold articles (no tools) | reference / oracle ceiling |

The judge (Opus 4.7) scores AGENT and Track A against Track B on five 0–2 dimensions: accuracy, faithfulness, citations, refusal_calibration, premise_handling. `overall_pass` requires accuracy≥1, faithfulness=2, citations≥1, refusal≥1, premise≥1.

## Useful flags

```bash
--prompt v2                         # try a different system prompt (src/wiki_eval/prompts/v2.md)
--limit 5                           # smoke run on first N cases
--cases simple-01,multihop-03       # run a specific subset
--workers 8                         # outer parallelism (default 8)
--no-cache                          # ignore the QA cache (rare — see below)
```

## Caching — important

Track A (closed-book) and Track B (oracle) answers are cached in `eval/_qa_cache.json`, keyed by case id with a fingerprint over `(question + gold_articles + closed-book model + reference model)`. They are reused across prompt versions because they don't depend on the agent prompt. This is what makes prompt iteration cheap: only the AGENT track + 2 judge calls re-run.

Use `--no-cache` only when you've changed the closed-book or reference model, or when debugging the cache itself. Don't use it to "force a fresh run" — the cache invalidates automatically when the question or gold articles change.

## Reading a report

The `.md` report has four sections worth checking, in order:

1. **Track comparison** — the headline 3×6 table. Look at the AGENT vs Track A gap on accuracy/faithfulness/refusal — that gap *is* the retrieval value.
2. **By category** — which question types the agent struggles with. If `multi_constraint` is at 50% pass and `simple_factual` at 95%, the prompt needs help with composition.
3. **Failure-mode counts** — judge tags like `missing_citation`, `wrong_fact`, `incomplete`, `hallucinated`. The most-frequent tag tells you the next thing to fix.
4. **Failing cases** — for each fail, you get the question, the agent answer, the reference answer, the closed-book answer, and the judge's one-sentence comment. Read 3–5 of these before changing the prompt.

## Current baselines (prompt v1)

| Dataset | N | AGENT pass% | Track A pass% | Top failure mode |
|---|---:|---:|---:|---|
| test_cases.jsonl | 40 | 87.5 | 2.5 | `no_search` (6) |
| test_cases_movies.jsonl | 22 | 63.6 | 0.0 | `incomplete` / `missing_citation` (6 each) |
| test_cases_bridges.jsonl | 20 | 65.0 | 0.0 | `missing_citation` (7) |

The two harder evals (movies, bridges) are where prompt iteration earns its keep.

## When something breaks

- **Anthropic API errors** — runs continue and the case is recorded with `error` set. Re-run `--cases <id>` once the API recovers.
- **Wikipedia 429 / network errors** — tools return an `{"error": ...}` payload that the agent sees; usually it retries with a different query. If a case fails with a `no_search` tag and the trace shows tool errors, re-run.
- **Judge JSON parse error** — shows up as a `PARSE_ERROR` comment in the case. Re-run the single case; non-deterministic and rare.
