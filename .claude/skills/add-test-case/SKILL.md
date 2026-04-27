---
name: add-test-case
description: Author a new test case for one of the wiki-eval datasets, with the post-cutoff filter and good question-design heuristics.
---

# add-test-case

Use this when adding a case to `eval/test_cases.jsonl`, `eval/test_cases_movies.jsonl`, or `eval/test_cases_bridges.jsonl`.

## Schema

One JSON object per line:

```json
{
  "id": "simple-41",
  "category": "simple_factual",
  "question": "Which country did Hurricane Melissa make a catastrophic landfall in during October 2025?",
  "gold_articles": ["Hurricane Melissa (2025)"],
  "expected_behavior": "Jamaica. Late October 2025."
}
```

| field | required | notes |
|---|---|---|
| `id` | yes | unique within the file. Use the dataset prefix (`simple-`, `multihop-`, `movie-`, `bridge-`). |
| `category` | yes | drives the per-category breakdown in the report. Pick from existing categories or coin a new one (anything snake_case). |
| `question` | yes | what gets asked of all three tracks. |
| `gold_articles` | yes | exact Wikipedia titles whose text grounds the reference (Track B). The article cache fetches these directly. |
| `expected_behavior` | yes | one or two sentences describing what a passing answer looks like. The judge sees this. |

The runner ignores blank lines, but keep the file well-formed JSONL — one object per line, no trailing comma.

## The post-cutoff rule

**Every case must require information not in Sonnet 4.6's parametric memory.** In practice that means the gold article should describe an event, person, or work created/published after **2025-09-01**. If Sonnet can answer from training data, the AGENT vs Track A gap collapses and the case stops measuring retrieval value.

Before adding a case:

1. Check the article's first revision date. The discovery scripts already filter on this:
   - `eval/find_post_cutoff_pages.py` — most-popular post-cutoff articles
   - `eval/discover_movies.py` — recent films, with release-date filter
   - `eval/discover_bridges.py` — `Category:Bridges completed in 2026`
2. If you're hand-picking an article, hit https://en.wikipedia.org/wiki/<Title>?action=history and confirm the first edit is after 2025-09-01.

The 1836 Hayward earthquake case was a special case: the *event* is old but the *article* was created post-cutoff, so Sonnet still hasn't seen it. That's a valid construction.

## Question-design heuristics

Cases that *discriminate* between prompts (i.e. some prompts pass them, some don't) are the ones that drive iteration. The 100% pass-rate evals (stubs, earthquakes — both archived from active iteration) failed this test because their questions named the entity directly.

Make questions discriminating by adding any of:

- **Multi-hop**: answer requires fetching a *second* article the first didn't name. Example: a question about an earthquake whose answer requires reading the `Indian Plate` article.
- **Tie-breakers**: state a constraint that narrows ambiguous candidates. "The 2026 cable-stayed bridge over the Yangtze projected to be the second-longest in the world" disambiguates between several candidates.
- **Multi-constraint composition**: combine 2+ attributes (location + type + year + characteristic). The agent has to verify each.
- **False premises**: ask a question whose answer requires correcting the question. Example: "Which 2026 Australian suspension bridge has a main span over 1,000 m?" (no such bridge exists). Set `expected_behavior` to describe the correction; the judge scores `premise_handling` and `refusal_calibration`.
- **Numerical reasoning over fetched content**: ratios, comparisons, "by how many units does X exceed Y", deltas. Forces the agent to do math on retrieved facts.
- **Don't name the entity**: prefer "the Mw 7.5 thrust-fault earthquake off northeastern Honshu on 20 April 2026" over "the 2026 Sanriku earthquake". Naming makes search trivial; describing forces real retrieval.

## Categories currently in use

| dataset | categories |
|---|---|
| test_cases.jsonl | `simple_factual`, `multi_hop`, `temporal`, `false_premise`, `non_encyclopedic`, `disambiguation` |
| test_cases_movies.jsonl | `multi_actor_identity`, `semantic_plot`, `tiebreak_grossing` |
| test_cases_bridges.jsonl | `single_match`, `multi_constraint`, `tiebreak_longest`, `tiebreak_tallest`, `tiebreak_first`, `tiebreak_replacement`, `false_premise` |

Pick one of these unless you genuinely need a new bucket.

## Workflow

1. Find a candidate article (discovery script or hand-pick) and confirm post-cutoff.
2. Read the article. Pick a fact that a stranger asking the question would actually want.
3. Draft the question with at least one of the discriminators above.
4. Set `gold_articles` to the *minimum* set of titles whose text grounds the reference. For multi-hop, list both.
5. Write `expected_behavior` as 1–2 sentences. Include specific values (dates, names, numbers) — the judge uses this as a sanity check on the reference.
6. Append the line to the right `.jsonl` file. Smoke-run with `python -m eval.run_eval --cases <new-id> --cases-file <file>`.
7. If AGENT passes and Track A fails, the case is doing its job. If both pass, the question is too easy — rewrite to remove naming or add a constraint.
