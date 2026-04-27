# Eval report — 2026-04-27T19:30:17Z

- Prompt version: **v1**
- Agent model: `claude-sonnet-4-6`
- Judge model: `claude-opus-4-7`
- Cases: 20  (elapsed 76s)

## Track comparison

All three tracks answer the same questions. AGENT and Track A are scored against Track B with the same judge rubric (Opus 4.7). Track B is the gold reference and is shown as ground truth.

| Track | Setup | Pass% | Accuracy | Faithfulness | Citations | Refusal | Premise |
|---|---|---:|---:|---:|---:|---:|---:|
| **AGENT** | Sonnet 4.6 + Wikipedia tools | **100.0** | 2.0 | 2.0 | 2.0 | 2.0 | 2.0 |
| **Track A** | Sonnet 4.6, no tools / no internet (closed-book) | **0.0** | 0.4 | 1.5 | 0.0 | 0.4 | 1.85 |
| **Track B** | Opus 4.7 reading the gold articles | _reference_ | _2.0_ | _2.0_ | _2.0_ | _2.0_ | _2.0_ |

Track A's citations score is structurally near-zero: a closed-book model cannot cite Wikipedia articles by construction. The informative comparison is on accuracy, faithfulness, refusal calibration, and premise handling.

## By category (AGENT vs Track A)

| Category | N | AGENT pass% | AGENT acc | Track A pass% | Track A acc |
|---|---:|---:|---:|---:|---:|
| casualty_impact | 3 | 100.0 | 2.0 | 0.0 | 0.0 |
| comparative_attribute | 1 | 100.0 | 2.0 | 0.0 | 1.0 |
| comparative_numerical | 1 | 100.0 | 2.0 | 0.0 | 1.0 |
| infobox_attribute | 8 | 100.0 | 2.0 | 0.0 | 0.0 |
| tectonic_attribute | 5 | 100.0 | 2.0 | 0.0 | 1.0 |
| tectonic_multihop | 1 | 100.0 | 2.0 | 0.0 | 1.0 |
| tsunami | 1 | 100.0 | 2.0 | 0.0 | 0.0 |

## Tool use

- avg_searches: 2.2
- avg_fetches: 1.45
- avg_turns: 3.1
- avg_input_tokens: 8755.75
- avg_output_tokens: 506.3

## Failure-mode counts

- (none)

## Failing cases

(All cases passed.)
