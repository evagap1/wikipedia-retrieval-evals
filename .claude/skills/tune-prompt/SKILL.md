---
name: tune-prompt
description: Iterate the wiki-eval system prompt — add a new version, drive it from failure-mode counts, and keep iteration cheap.
---

# tune-prompt

Use this when you want to improve AGENT pass rate on the harder evals (movies, bridges) by editing the system prompt, not the agent code.

## Where prompts live

```
src/wiki_eval/prompts/
  v1.md     # current default
```

Each file is a complete system prompt. `wiki_eval.agent.load_prompt(version)` resolves `<version>.md` from that directory. To try a new prompt, drop in `v2.md` and run:

```bash
python -m eval.run_eval --prompt v2 --cases-file eval/test_cases_movies.jsonl
```

Reports are stamped per prompt version (e.g. `..._test_cases_movies_v2.md`) so you can diff against `_v1.md` directly.

## Drive iteration from the failure-mode counts

The judge tags each fail with one or more of:
`hallucinated`, `missing_citation`, `wrong_citation`, `missed_refusal`, `false_premise_accepted`, `incomplete`, `wrong_fact`, `over_refused`, `padded`, `no_search`.

The **most-frequent tag in the report is your next prompt change**. Map them to fixes:

| top failure mode | likely root cause | what to add to the prompt |
|---|---|---|
| `no_search` | model answered from memory | strengthen "ground every claim in Wikipedia, do not answer from memory" rule, with an example |
| `missing_citation` | inline `[Title]` markers absent | restate the citation format, give a worked example, require citations after every factual sentence |
| `wrong_citation` | citing an article that wasn't fetched | "only cite articles you fetched, not search snippets" |
| `incomplete` | multi-part question only partially answered | "for multi-part questions, enumerate sub-questions before searching, and check you've answered each before stopping" |
| `wrong_fact` | misread the fetched article | "when an article is long, fetch with higher max_chars rather than guessing from the lead" |
| `false_premise_accepted` | took the question at face value | add explicit false-premise check; require the model to verify the premise before answering |
| `over_refused` | refused when answer was findable | tone down hedging — "refuse only when Wikipedia genuinely doesn't cover the question, not when it's hard to find" |
| `hallucinated` | invented details outside fetched text | tighten faithfulness rule with negative example |
| `padded` | extraneous prose | "answer in ≤120 words unless the question requires more" |

## Keep iteration cheap

Track A and Track B answers are cached per case (see `run-eval` skill). So **prompt iteration only re-runs the AGENT track + 2 judge calls**. A v1→v2→v3 cycle on the bridges eval (20 cases) is ~90 s each.

Don't pass `--no-cache`. It re-runs Track A and Track B, which is wasted work — neither depends on the prompt.

## Workflow

1. Pick the dataset where the gap to ceiling is largest. Right now: movies (63.6%) and bridges (65%).
2. Open the latest `_v1.md` report. Read the **failure-mode counts** and 3–5 **failing cases** (the report includes the full agent answer + reference for each).
3. Make a hypothesis: *"the agent is dropping citations on multi-constraint questions because the format example only shows single-fact answers"*.
4. Copy `v1.md` to `v2.md`. Make a **single, targeted change** that addresses the hypothesis.
5. Run `python -m eval.run_eval --prompt v2 --cases-file <file>`.
6. Compare `_v2.md` report to `_v1.md`. Look at:
   - overall pass% and accuracy delta
   - the targeted failure-mode count (did it drop?)
   - whether other failure modes got *worse* (regressions)
7. If v2 is strictly better, keep it. If it's a wash, throw it away. If one mode got worse, look at which cases regressed and decide if the trade is worth it.
8. Repeat — but resist the urge to bundle multiple changes into one version. Single-change versions make regressions diagnosable.

## When to change something other than the prompt

The prompt cannot fix:

- **Tool affordances** — if the failure is "agent kept fetching with default 4000 chars and missed the answer", that's a tool default. Edit `src/wiki_eval/tools.py`.
- **Tool budget** — if the failure is "max turns reached", bump `DEFAULT_MAX_TURNS` in `agent.py`.
- **Question quality** — if a case fails for both v1 and v2 and the judge comment is "expected behavior is ambiguous", fix the case, not the prompt.

If you're 3 prompt versions deep on the same failure mode, the problem is likely outside the prompt.

## Don't iterate on these

The original eval (`test_cases.jsonl`, 87.5% AGENT) is mostly saturated for prompt-only fixes. The 6 `no_search` failures there are real but they tend to come back with new prompts unless you explicitly forbid memory-only answers — which then trades for `over_refused` on easy cases. Treat `test_cases.jsonl` as a regression check; do active hill-climbing on movies and bridges.

## Auto-improver (no eval access)

For exploration without rerunning evals, use the meta-agent in `src/wiki_eval/improve_prompt.py`. It uses Opus 4.7 to critique the current prompt for missing edge cases, conflicts, and vague rules — then applies a single targeted fix per round.

```bash
python -m wiki_eval.improve_prompt --base v2 --rounds 3
# writes v3.md, v4.md, v5.md
python -m wiki_eval.improve_prompt --base v2 --rounds 1 --dry-run
# print the critique without writing anything
```

Caveats:
- It cannot measure improvement — only proposes plausible improvements. Run the eval afterward to verify.
- It tends to plateau or oscillate after 2–3 rounds. Don't crank `--rounds` to 10 expecting linear gains.
- Each round's revision is anchored to the previous round's output, so a bad early revision compounds. If round 1 looks off, restart from `--base v2` rather than continuing.

Use it as an idea generator. The single-change discipline above still applies — pick the auto-generated version that addresses the failure mode you actually see in the report, not all of them.
