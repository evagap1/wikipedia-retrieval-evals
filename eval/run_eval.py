"""End-to-end eval runner.

For each test case:

    1. Run the wiki-eval agent (Track AGENT — Sonnet 4.6 + Wikipedia tools).
    2. Generate the closed-book baseline (Track A — Sonnet 4.6, NO tools, NO
       internet, parametric knowledge only).
    3. Generate the open-book reference (Track B — Opus 4.7 reading the gold
       articles only — no tools either; we paste article text into the prompt).
    4. Score AGENT vs Track B with the LLM judge (Opus 4.7).

Writes timestamped JSON + markdown reports to eval/reports/.

Usage:

    python -m eval.run_eval                  # all 40 cases, prompt v1
    python -m eval.run_eval --prompt v2      # try a different prompt
    python -m eval.run_eval --limit 5        # quick smoke run
    python -m eval.run_eval --workers 8      # more parallelism
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv

from eval.judge import (
    CLOSED_BOOK_MODEL,
    REFERENCE_MODEL,
    closed_book_answer,
    judge_answer,
    make_client,
    reference_answer,
)
from wiki_eval.agent import run_agent
from wiki_eval.tools import fetch_wikipedia_article

CASES_PATH = Path(__file__).parent / "test_cases.jsonl"
REPORTS_DIR = Path(__file__).parent / "reports"
QA_CACHE_PATH = Path(__file__).parent / "_qa_cache.json"


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    cases = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        cases.append(json.loads(line))
    return cases


# ---------------------------------------------------------------------------
# Article cache (gold articles are reused across many cases)
# ---------------------------------------------------------------------------


class ArticleCache:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._lock = Lock()

    def get(self, title: str) -> str:
        with self._lock:
            if title in self._cache:
                return self._cache[title]
        result = fetch_wikipedia_article(title, max_chars=8000)
        text = result.get("text") or f"[fetch error: {result.get('error', 'unknown')}]"
        with self._lock:
            self._cache[title] = text
        return text


# ---------------------------------------------------------------------------
# Question-level cache: closed-book and reference answers don't depend on the
# agent prompt version, so we cache them across runs. Iterating v1 → v2 → v3
# only re-runs the agent + judges, not the baselines. Big speedup.
# ---------------------------------------------------------------------------


class QACache:
    """Persistent cache for Track A and Track B answers.

    Keyed by case id. A fingerprint of (question + gold articles + models)
    invalidates the entry if any of those change.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        if path.exists():
            try:
                self._data: dict[str, dict[str, Any]] = json.loads(path.read_text())
            except json.JSONDecodeError:
                self._data = {}
        else:
            self._data = {}

    @staticmethod
    def fingerprint(question: str, gold: list[str]) -> str:
        h = hashlib.sha256()
        h.update(question.encode("utf-8"))
        h.update(b"|")
        h.update("|".join(gold).encode("utf-8"))
        h.update(b"|")
        h.update(CLOSED_BOOK_MODEL.encode("utf-8"))
        h.update(b"|")
        h.update(REFERENCE_MODEL.encode("utf-8"))
        return h.hexdigest()[:16]

    def get(self, case_id: str, fp: str, key: str) -> str | None:
        with self._lock:
            entry = self._data.get(case_id)
            if entry and entry.get("fingerprint") == fp:
                return entry.get(key)
            return None

    def set(self, case_id: str, fp: str, key: str, value: str) -> None:
        with self._lock:
            entry = self._data.get(case_id)
            if not entry or entry.get("fingerprint") != fp:
                entry = {"fingerprint": fp}
                self._data[case_id] = entry
            entry[key] = value

    def save(self) -> None:
        with self._lock:
            self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Per-case work
# ---------------------------------------------------------------------------


def run_case(
    case: dict[str, Any],
    *,
    prompt_version: str,
    article_cache: ArticleCache,
    qa_cache: QACache,
    judge_client,
    inner_pool: ThreadPoolExecutor,
) -> dict[str, Any]:
    case_id = case["id"]
    question = case["question"]
    expected = case.get("expected_behavior", "")
    gold = case.get("gold_articles", [])

    t0 = time.monotonic()
    out: dict[str, Any] = {"case": case, "prompt_version": prompt_version}
    fp = qa_cache.fingerprint(question, gold)

    # ----- Phase 1: kick off agent + Track A + Track B in parallel -----

    def _run_agent() -> tuple[str, list[str], dict[str, Any]]:
        run = run_agent(question, prompt_version=prompt_version)
        ans = run.answer
        fetched = [
            tc.arguments.get("title", "")
            for tc in run.tool_calls
            if tc.name == "fetch_wikipedia_article"
        ]
        return ans, fetched, run.to_dict()

    def _closed_book() -> str:
        cached = qa_cache.get(case_id, fp, "closed_book_answer")
        if cached is not None:
            return cached
        ans = closed_book_answer(judge_client, question)
        qa_cache.set(case_id, fp, "closed_book_answer", ans)
        return ans

    def _reference() -> str:
        cached = qa_cache.get(case_id, fp, "reference_answer")
        if cached is not None:
            return cached
        article_texts = {t: article_cache.get(t) for t in gold}
        ans = reference_answer(judge_client, question, gold, article_texts)
        qa_cache.set(case_id, fp, "reference_answer", ans)
        return ans

    f_agent = inner_pool.submit(_run_agent)
    f_a = inner_pool.submit(_closed_book)
    f_b = inner_pool.submit(_reference)

    try:
        agent_answer, fetched_titles, agent_dict = f_agent.result()
        out["agent_run"] = agent_dict
    except Exception as e:
        out["error"] = f"agent failed: {e}"
        out["agent_run"] = None
        agent_answer = ""
        fetched_titles = []

    try:
        out["closed_book_answer"] = f_a.result()
    except Exception as e:
        out["closed_book_answer"] = f"[error: {e}]"

    try:
        out["reference_answer"] = f_b.result()
    except Exception as e:
        out["reference_answer"] = f"[error: {e}]"

    # ----- Phase 2: judge AGENT and Track A in parallel -----

    def _judge(answer: str, fetched: list[str]) -> dict[str, Any]:
        try:
            return judge_answer(
                judge_client,
                question=question,
                expected_behavior=expected,
                agent_answer=answer,
                reference_answer_text=out["reference_answer"],
                fetched_titles=fetched,
            ).to_dict()
        except Exception as e:
            return {"error": str(e)}

    f_j_agent = inner_pool.submit(_judge, agent_answer, fetched_titles)
    f_j_a = inner_pool.submit(_judge, out["closed_book_answer"], [])
    out["judge"] = f_j_agent.result()
    out["closed_book_judge"] = f_j_a.result()

    out["case_latency_ms"] = int((time.monotonic() - t0) * 1000)
    j = out.get("judge", {}) or {}
    cb = out.get("closed_book_judge", {}) or {}
    print(
        f"  [{case_id}] "
        f"AGENT pass={j.get('overall_pass')} acc={j.get('accuracy')}/2  "
        f"TrackA pass={cb.get('overall_pass')} acc={cb.get('accuracy')}/2  "
        f"({out['case_latency_ms']}ms)",
        flush=True,
    )
    return out


# ---------------------------------------------------------------------------
# Aggregation + reports
# ---------------------------------------------------------------------------


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_cat[r["case"]["category"]].append(r)

    def _scores(rs: list[dict[str, Any]], judge_key: str, score_key: str) -> list[int]:
        return [
            r[judge_key][score_key]
            for r in rs
            if isinstance(r.get(judge_key), dict) and score_key in r[judge_key]
        ]

    def avg(rs: list[dict[str, Any]], score_key: str, judge_key: str = "judge") -> float:
        s = _scores(rs, judge_key, score_key)
        return round(sum(s) / len(s), 2) if s else 0.0

    def pass_rate(rs: list[dict[str, Any]], judge_key: str = "judge") -> float:
        if not rs:
            return 0.0
        passes = sum(1 for r in rs if (r.get(judge_key) or {}).get("overall_pass"))
        return round(100 * passes / len(rs), 1)

    def track_summary(judge_key: str) -> dict[str, Any]:
        return {
            "pass_rate": pass_rate(results, judge_key),
            "avg_accuracy": avg(results, "accuracy", judge_key),
            "avg_faithfulness": avg(results, "faithfulness", judge_key),
            "avg_citations": avg(results, "citations", judge_key),
            "avg_refusal_calibration": avg(results, "refusal_calibration", judge_key),
            "avg_premise_handling": avg(results, "premise_handling", judge_key),
        }

    summary = {
        "n_cases": len(results),
        # AGENT track (Sonnet 4.6 + Wikipedia tools)
        "agent": track_summary("judge"),
        # Track A (Sonnet 4.6, NO tools / NO internet) — same rubric, same reference
        "closed_book": track_summary("closed_book_judge"),
        "by_category": {
            cat: {
                "n": len(rs),
                "agent_pass_rate": pass_rate(rs, "judge"),
                "agent_accuracy": avg(rs, "accuracy", "judge"),
                "closed_book_pass_rate": pass_rate(rs, "closed_book_judge"),
                "closed_book_accuracy": avg(rs, "accuracy", "closed_book_judge"),
            }
            for cat, rs in sorted(by_cat.items())
        },
        "failure_modes": _failure_mode_counts(results),
        "tool_use_stats": _tool_use_stats(results),
    }
    # Back-compat top-level fields (so old report consumers still work)
    summary["overall_pass_rate"] = summary["agent"]["pass_rate"]
    summary["avg_accuracy"] = summary["agent"]["avg_accuracy"]
    summary["avg_faithfulness"] = summary["agent"]["avg_faithfulness"]
    summary["avg_citations"] = summary["agent"]["avg_citations"]
    summary["avg_refusal_calibration"] = summary["agent"]["avg_refusal_calibration"]
    summary["avg_premise_handling"] = summary["agent"]["avg_premise_handling"]
    return summary


def _failure_mode_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        for fm in r.get("judge", {}).get("failure_modes", []) or []:
            counts[fm] += 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _tool_use_stats(results: list[dict[str, Any]]) -> dict[str, float]:
    n_search, n_fetch, turns, in_tok, out_tok = [], [], [], [], []
    for r in results:
        run = r.get("agent_run") or {}
        tcs = run.get("tool_calls") or []
        n_search.append(sum(1 for tc in tcs if tc["name"] == "search_wikipedia"))
        n_fetch.append(sum(1 for tc in tcs if tc["name"] == "fetch_wikipedia_article"))
        turns.append(run.get("turns", 0))
        in_tok.append(run.get("input_tokens", 0))
        out_tok.append(run.get("output_tokens", 0))

    def m(xs: list[int]) -> float:
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    return {
        "avg_searches": m(n_search),
        "avg_fetches": m(n_fetch),
        "avg_turns": m(turns),
        "avg_input_tokens": m(in_tok),
        "avg_output_tokens": m(out_tok),
    }


def render_markdown(
    summary: dict[str, Any], results: list[dict[str, Any]], meta: dict[str, Any]
) -> str:
    lines: list[str] = []
    lines.append(f"# Eval report — {meta['timestamp']}")
    lines.append("")
    lines.append(f"- Prompt version: **{meta['prompt_version']}**")
    lines.append(f"- Agent model: `{meta['agent_model']}`")
    lines.append(f"- Judge model: `{meta['judge_model']}`")
    lines.append(f"- Cases: {summary['n_cases']}  (elapsed {meta['elapsed_s']}s)")
    lines.append("")
    lines.append("## Track comparison")
    lines.append("")
    lines.append(
        "All three tracks answer the same questions. AGENT and Track A are "
        "scored against Track B with the same judge rubric (Opus 4.7). "
        "Track B is the gold reference and is shown as ground truth."
    )
    lines.append("")
    a = summary["agent"]
    cb = summary["closed_book"]
    lines.append("| Track | Setup | Pass% | Accuracy | Faithfulness | Citations | Refusal | Premise |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| **AGENT** | Sonnet 4.6 + Wikipedia tools | "
        f"**{a['pass_rate']}** | {a['avg_accuracy']} | {a['avg_faithfulness']} | "
        f"{a['avg_citations']} | {a['avg_refusal_calibration']} | {a['avg_premise_handling']} |"
    )
    lines.append(
        f"| **Track A** | Sonnet 4.6, no tools / no internet (closed-book) | "
        f"**{cb['pass_rate']}** | {cb['avg_accuracy']} | {cb['avg_faithfulness']} | "
        f"{cb['avg_citations']} | {cb['avg_refusal_calibration']} | {cb['avg_premise_handling']} |"
    )
    lines.append(
        f"| **Track B** | Opus 4.7 reading the gold articles | "
        f"_reference_ | _2.0_ | _2.0_ | _2.0_ | _2.0_ | _2.0_ |"
    )
    lines.append("")
    lines.append(
        "Track A's citations score is structurally near-zero: a closed-book "
        "model cannot cite Wikipedia articles by construction. The "
        "informative comparison is on accuracy, faithfulness, refusal "
        "calibration, and premise handling."
    )
    lines.append("")
    lines.append("## By category (AGENT vs Track A)")
    lines.append("")
    lines.append("| Category | N | AGENT pass% | AGENT acc | Track A pass% | Track A acc |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for cat, s in summary["by_category"].items():
        lines.append(
            f"| {cat} | {s['n']} | {s['agent_pass_rate']} | {s['agent_accuracy']} | "
            f"{s['closed_book_pass_rate']} | {s['closed_book_accuracy']} |"
        )
    lines.append("")
    lines.append("## Tool use")
    lines.append("")
    for k, v in summary["tool_use_stats"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Failure-mode counts")
    lines.append("")
    if summary["failure_modes"]:
        for tag, n in summary["failure_modes"].items():
            lines.append(f"- {tag}: {n}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Failing cases")
    lines.append("")
    any_failures = False
    for r in results:
        j = r.get("judge", {})
        if j.get("overall_pass"):
            continue
        any_failures = True
        case = r["case"]
        lines.append(f"### {case['id']} ({case['category']})")
        lines.append("")
        lines.append(f"**Q:** {case['question']}")
        lines.append("")
        lines.append(
            f"- accuracy={j.get('accuracy')} faithfulness={j.get('faithfulness')} "
            f"citations={j.get('citations')} refusal={j.get('refusal_calibration')} "
            f"premise={j.get('premise_handling')}"
        )
        lines.append(f"- failure_modes: {j.get('failure_modes')}")
        lines.append(f"- judge comment: {j.get('comment')}")
        lines.append("")
        lines.append("**Agent answer:**")
        lines.append("")
        agent_ans = (r.get("agent_run") or {}).get("answer") or "(empty)"
        lines.append("> " + agent_ans.replace("\n", "\n> "))
        lines.append("")
        lines.append("**Reference answer:**")
        lines.append("")
        lines.append("> " + (r.get("reference_answer", "") or "").replace("\n", "\n> "))
        lines.append("")
        lines.append("**Closed-book answer (Track A, no tools / no internet):**")
        lines.append("")
        lines.append("> " + (r.get("closed_book_answer", "") or "").replace("\n", "\n> "))
        lines.append("")
    if not any_failures:
        lines.append("(All cases passed.)")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="v1", help="Prompt version (default: v1).")
    ap.add_argument("--limit", type=int, default=None, help="Run only first N cases.")
    ap.add_argument("--workers", type=int, default=8, help="Parallel cases (default: 8).")
    ap.add_argument("--cases", default=None, help="Comma-separated case IDs to run.")
    ap.add_argument(
        "--cases-file",
        default=str(CASES_PATH),
        help="Path to a JSONL file of test cases (default: eval/test_cases.jsonl).",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore the QA cache and re-generate Track A and Track B from scratch.",
    )
    args = ap.parse_args(argv)

    load_dotenv()

    cases = load_cases(Path(args.cases_file))
    if args.cases:
        wanted = set(c.strip() for c in args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    print(
        f"Running {len(cases)} cases  prompt={args.prompt}  workers={args.workers}"
    )
    article_cache = ArticleCache()
    qa_cache_path = QA_CACHE_PATH
    if args.no_cache and qa_cache_path.exists():
        # Don't delete it; just load into a throwaway path-less cache
        qa_cache = QACache(Path("/tmp/_throwaway_qa_cache.json"))
        qa_cache._data = {}
    else:
        qa_cache = QACache(qa_cache_path)
    cache_hits_before = sum(
        1 for c in cases if qa_cache.get(c["id"], qa_cache.fingerprint(c["question"], c.get("gold_articles", [])), "reference_answer")
    )
    print(f"QA cache: {cache_hits_before}/{len(cases)} cases have a cached reference + closed-book")

    judge_client = make_client()

    t0 = time.monotonic()
    results: list[dict[str, Any]] = []
    # Outer pool runs cases in parallel. Inner pool fans out the 3 tracks
    # (agent / Track A / Track B) and the 2 judges within each case. Sized
    # generously since most threads spend their time waiting on HTTP.
    inner_pool = ThreadPoolExecutor(max_workers=max(3, args.workers))
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [
                ex.submit(
                    run_case,
                    c,
                    prompt_version=args.prompt,
                    article_cache=article_cache,
                    qa_cache=qa_cache,
                    judge_client=judge_client,
                    inner_pool=inner_pool,
                )
                for c in cases
            ]
            for f in futures:
                results.append(f.result())
    finally:
        inner_pool.shutdown(wait=True)
        qa_cache.save()
    elapsed = int(time.monotonic() - t0)

    case_index = {c["id"]: i for i, c in enumerate(cases)}
    results.sort(key=lambda r: case_index[r["case"]["id"]])

    summary = aggregate(results)
    meta = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_version": args.prompt,
        "agent_model": "claude-sonnet-4-6",
        "judge_model": "claude-opus-4-7",
        "elapsed_s": elapsed,
        "n_cases": len(results),
    }

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cases_tag = Path(args.cases_file).stem  # e.g. "test_cases" or "test_cases_movies"
    json_path = REPORTS_DIR / f"{stamp}_{cases_tag}_{args.prompt}.json"
    md_path = REPORTS_DIR / f"{stamp}_{cases_tag}_{args.prompt}.md"
    json_path.write_text(
        json.dumps({"meta": meta, "summary": summary, "results": results}, indent=2, ensure_ascii=False)
    )
    md_path.write_text(render_markdown(summary, results, meta))

    (REPORTS_DIR / "latest.json").write_text(json_path.read_text())
    (REPORTS_DIR / "latest.md").write_text(md_path.read_text())

    print()
    print(f"Done in {elapsed}s.")
    print(f"  Pass rate: {summary['overall_pass_rate']}%")
    print(
        f"  Avg accuracy: {summary['avg_accuracy']}/2  "
        f"faith: {summary['avg_faithfulness']}/2  "
        f"cite: {summary['avg_citations']}/2"
    )
    print("  Reports:")
    print(f"    {json_path}")
    print(f"    {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
