# wiki-eval — design rationale

This is the writeup for the wiki-eval prototype. It explains the choices behind the system, how the eval is structured, what the numbers say, and where the system breaks.

## What the system is

A Wikipedia-grounded Q&A agent built on the Anthropic API, with an eval harness that grades it on factual accuracy, faithfulness to Wikipedia, citation quality, refusal calibration, and false-premise handling.

- **Agent model:** `claude-sonnet-4-6` with two tools — `search_wikipedia` and `fetch_wikipedia_article` — backed directly by the live MediaWiki API. No hosted retrieval, no vector store, no scraping.
- **Judge model:** `claude-opus-4-7` with a structured 0–2 rubric returned as JSON.
- **Eval suite:** three datasets (82 cases total) plus a closed-book baseline and an oracle reference, run on every case for a 3-track comparison.

The whole thing is ~1k lines of Python plus three Markdown prompt files.

## Why these models

- Sonnet 4.6 as the agent is the cost/quality sweet spot for tool-using agents. The tasks here are mostly retrieval + composition; Opus would be over-spec.
- Opus 4.7 as the judge because grading is a *harder* task than answering — the judge has to weigh agent answer vs. reference and distinguish "missing a key element" (acc=1) from "factually wrong on the main point" (acc=0). The cost is fine because we cache the judge's static system prompt with `cache_control: ephemeral` so repeated calls within an eval run are cheap.
- Opus 4.7 as the reference (Track B) for the same reason: it has to faithfully extract from gold article text without inventing.

## The agent loop

`src/wiki_eval/agent.py` runs the standard Anthropic tool-use loop:

1. Send the question with the `tools=` list.
2. While `stop_reason == "tool_use"`: dispatch each `tool_use` block locally, append the `tool_result` blocks to messages, repeat. Hard-capped at 10 turns.
3. When the model emits a `text` block as its final content, that's the answer.

The full trace (each tool call, args, result summary, latency, token counts) is preserved on `AgentRun` and serialized into the eval report. This lets the evaluation grade not just *what* the agent said but *how* it got there — `no_search` and `wrong_citation` are recoverable as failure modes only because we have the trace.

## Tools

Two tools, both thin wrappers over the MediaWiki API at `https://en.wikipedia.org/w/api.php`:

- `search_wikipedia(query, limit=5)` — `list=search` endpoint, returns titles + plaintext snippets.
- `fetch_wikipedia_article(title, max_chars=4000)` — `prop=extracts&explaintext=1`, with `redirects=1`. Caps the slice fed back to the model to protect the context window; the model can re-fetch with a larger `max_chars` (up to 12000) if it sees `[...article truncated]`.

The model gets clean prose, not wiki markup. Snippet HTML (`<span class="searchmatch">`) is stripped before the result is returned.

## Why three tracks

A single pass-rate for the agent is a useless number on its own. It conflates "the agent did good retrieval" with "the model already knew the answer from training data". The 3-track design separates these:

| Track | Setup | What it measures |
|---|---|---|
| **AGENT** | Sonnet 4.6 + Wikipedia tools | the system under test |
| **Track A** | Sonnet 4.6, *no tools, no internet, parametric only* | what the same model gets without retrieval |
| **Track B** | Opus 4.7 reading the gold articles (text pasted into the prompt, no tools either) | the oracle ceiling — what's achievable when the right articles are in hand |

The same judge rubric scores AGENT and Track A against Track B as the reference. The **AGENT − Track A** gap is the retrieval value. The **Track B − AGENT** gap is the headroom — what better retrieval or prompting could still recover.

This is why the cases are post-cutoff (see next section). If the Track A model already knows the answer, the gap collapses and you're just measuring the model.

### Closed-book really means closed-book

`closed_book_answer()` in `eval/judge.py` calls `client.messages.create(...)` *without* a `tools=` argument. The Anthropic API only enables tool use (including any hosted tools like web search) when `tools` is present, so the model is genuinely answering from parametric memory. The system prompt also forbids it from pretending to look anything up. The intent is documented inline so it can't drift:

```python
# Intentionally no `tools=` — closed-book means closed-book.
```

## The post-cutoff filter

The eval is intentionally biased toward content created after **2025-09-01** — Sonnet 4.6's knowledge cutoff is January 2026 in name but practical recall on events from late 2025 onward is patchy. By grounding cases in articles created (or in the case of bridges/movies, describing events occurring) after that date, Track A *should* fail. When it doesn't, the case is too easy and gets reworked.

Discovery is automated:

- `eval/find_post_cutoff_pages.py` — uses `srsort=create_timestamp_desc` on broad probes, then verifies each candidate's first-revision timestamp, then filters on incoming-link count and pageviews. Output is the candidate list that seeded the original 40 cases.
- `eval/discover_movies.py` + `eval/extract_movie_facts.py` — pulls recent film categories and parses release dates from the lead.
- `eval/discover_bridges.py` — walks `Category:Bridges completed in 2026` and its country sub-categories.

Each script saves both a structured summary (`_summary.json`) and the full article text per candidate (`_*_corpus/*.txt`), so case authoring references real content instead of training-data recall.

## The judge rubric

`eval/judge.py` defines a 0–2 integer rubric over five dimensions:

| dimension | what 0 means | what 2 means |
|---|---|---|
| **accuracy** | factually wrong on the main point | fully correct |
| **faithfulness** | clearly hallucinates / invents details | every claim supported by the reference |
| **citations** | no inline `[Title]` and no Sources line | inline citations *and* Sources list naming actually-fetched articles |
| **refusal_calibration** | answered confidently when it should have refused (or refused when it shouldn't) | perfect calibration |
| **premise_handling** | accepted a false premise | corrected explicitly |

`overall_pass` is a hard conjunction: `accuracy ≥ 1 AND faithfulness == 2 AND citations ≥ 1 AND refusal_calibration ≥ 1 AND premise_handling ≥ 1`. Faithfulness gets the strict `== 2` because hallucination is the failure mode that destroys trust. The rest are gated at "passable" rather than "perfect" so high-quality answers with one missing citation still pass.

The judge also emits structured `failure_modes` tags (`hallucinated`, `missing_citation`, `wrong_fact`, `incomplete`, …). Aggregated counts in the report tell us *which lever to pull next* — see `.claude/skills/tune-prompt/SKILL.md` for the failure-mode → prompt-change mapping.

The judge's static system prompt (~2.5k tokens of rubric definition) is wrapped in `cache_control: {type: ephemeral}` so repeated calls within an eval run hit the cache. Track B's static system prompt is short enough not to need caching.

## The eval datasets we ship

Three active datasets, 82 cases total:

| File | N | Focus | AGENT pass% (v1) | Track A pass% | Headroom |
|---|---:|---|---:|---:|---|
| `test_cases.jsonl` | 40 | broad post-cutoff facts: simple, multi-hop, temporal, false-premise, non-encyclopedic, disambiguation | 87.5 | 2.5 | mostly saturated; regression check |
| `test_cases_movies.jsonl` | 22 | 2025–26 films; multi-actor identity (find the film by overlapping cast) and semantic plot lookup, with grossing tie-breakers | 63.6 | 0.0 | active iteration target |
| `test_cases_bridges.jsonl` | 20 | 2026 bridges; multi-constraint composition (location + type + characteristic + tie-breaker), longest/tallest/first/replacement tie-breaks, false premises | 65.0 | 0.0 | active iteration target |

Two further datasets (`test_cases_stubs.jsonl`, `test_cases_earthquake.jsonl`) were generated and run but are not part of the active hill-climbing suite. Both reached 100% AGENT pass on v1, which means they don't discriminate between prompt versions. They remain in the repo as smoke tests but the active iteration loop runs only the three above.

The pattern across the three active sets is consistent and what we'd hope for:

- **Track A pass% ≈ 0** on movies/bridges and only 2.5% on the original 40 — the post-cutoff filter is doing its job.
- **AGENT pass% gap of +60 to +85 points** quantifies retrieval value cleanly.
- **Track B pass% would be ~100% by construction** (it reads the gold article); we record it as the ceiling.

## What v1 fails on

Per-failure-mode counts on the 82-case suite (prompt v1):

- `missing_citation` — most common across movies/bridges. The agent answers correctly but drops the inline `[Title]` markers, especially on multi-part answers where the format example in v1 doesn't show how to attribute multiple facts to multiple articles.
- `incomplete` — multi-constraint questions: agent satisfies 2 of 3 constraints and stops. Suggests adding "before stopping, verify you've answered every sub-question" to the prompt.
- `no_search` — concentrated on the original 40, where 6 cases got answered from parametric memory despite the explicit "do not answer from memory" rule. Sonnet sometimes shortcuts on easy-looking questions.
- `wrong_fact` — small but persistent: the agent fetches the right article, then misreads it. Often a default-truncation issue: the answer was past 4000 chars and the model didn't re-fetch with a higher `max_chars`.
- `false_premise_accepted` — bridges has 2 cases that explicitly probe this. The agent took both at face value.

These map directly to specific prompt tweaks documented in the `tune-prompt` skill.

## What the system can't do (and why that's fine)

- **No retrieval over content not in Wikipedia.** This was a constraint, not an oversight — Wikipedia is the source of truth by design. Questions that need primary sources (court filings, raw datasets, social media) fall back to refusal.
- **No real-time data.** "What's the score right now?" — Wikipedia has no current state. The system is told to refuse these.
- **No translation.** English Wikipedia only. Articles that exist on other Wikipedias and not en-wiki return `Article not found`.
- **No hierarchical retrieval.** A question requiring a chain of 4+ article hops will eventually exhaust the 10-turn budget. The current eval has 1- and 2-hop cases; 3-hop+ is the next class up.

## What I'd build next

In rough priority order:

1. **v2 prompt** targeting `missing_citation` + `incomplete` on movies/bridges. Single-change versions, measured against v1 with the QA cache reused.
2. **Tool affordance: a per-question turn budget hint.** For multi-hop / multi-constraint cases the agent often plans well and then runs out of turns. Letting the planner emit an "expected hop count" the model can reason about would help.
3. **An infobox-aware fetch tool.** A surprising fraction of the bridges/earthquakes facts live in the infobox, which the plaintext extract preserves but doesn't structure. A second fetch mode that returns infobox key-values would shorten the path to a numerical answer.
4. **Evaluator-level cost tracking.** Currently the report has token counts but no $ figure. A small post-processor would let us track "AGENT tokens per pass" as the optimization target alongside pass rate.
5. **A regression gate.** A test that runs the cached v1 numbers and fails CI if the headline pass rate drops. Right now nothing prevents a prompt change from silently breaking the original 40 cases.

## Files of interest

```
src/wiki_eval/
  agent.py                 # tool-use loop, AgentRun trace
  tools.py                 # MediaWiki API wrappers + tool schemas
  prompts/v1.md            # system prompt
  cli.py                   # `wiki-eval ask`, `--demo`

eval/
  run_eval.py              # 3-track runner, parallelism, QA cache, report
  judge.py                 # closed-book, reference, judge — all here
  test_cases.jsonl         # 40 cases — original
  test_cases_movies.jsonl  # 22 cases — multi-actor + plot
  test_cases_bridges.jsonl # 20 cases — 2026 bridges
  discover_*.py            # candidate-finding scripts
  reports/                 # timestamped JSON + Markdown reports

.claude/skills/
  run-eval/SKILL.md        # how to run + read reports
  add-test-case/SKILL.md   # JSONL schema + post-cutoff rule + question-design heuristics
  tune-prompt/SKILL.md     # failure-mode → prompt-change mapping
```
