"""CLI: ask the agent a question or run the demo set."""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from wiki_eval.agent import AgentRun, run_agent

DEMO_QUESTIONS = [
    "Who designed the Eiffel Tower, and in what year was it completed?",
    "What was Marie Curie's first Nobel Prize awarded for, and who shared it with her?",
    "Which country has the longest coastline in the world?",
    "When did Einstein win the Nobel Prize for relativity?",  # false-premise probe
    "What is the capital of the country in which the inventor of the telephone was born?",
]


def _print_run(console: Console, run: AgentRun) -> None:
    # Tool trace
    if run.tool_calls:
        table = Table(title="Tool trace", show_lines=False, expand=False)
        table.add_column("#", style="dim")
        table.add_column("Tool")
        table.add_column("Args")
        table.add_column("Result", overflow="fold")
        table.add_column("ms", justify="right")
        for i, tc in enumerate(run.tool_calls, 1):
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            if "hits" in tc.result:
                titles = ", ".join(h["title"] for h in tc.result["hits"]) or "(none)"
                result_str = f"hits: {titles}"
            elif "text" in tc.result:
                result_str = f"fetched '{tc.result['title']}' ({tc.result['char_count']} chars)"
            elif "error" in tc.result:
                result_str = f"ERROR: {tc.result['error']}"
            else:
                result_str = json.dumps(tc.result)[:120]
            table.add_row(str(i), tc.name, args_str, result_str, str(tc.latency_ms))
        console.print(table)
    else:
        console.print("[yellow]No tool calls were made.[/yellow]")

    # Stats line
    stats = (
        f"turns={run.turns}  "
        f"in_tok={run.input_tokens}  out_tok={run.output_tokens}  "
        f"latency={run.total_latency_ms}ms  stop={run.stop_reason}"
    )
    console.print(f"[dim]{stats}[/dim]")

    # Answer
    if run.error:
        console.print(Panel(f"[red]{run.error}[/red]", title="Error"))
    body = run.answer or "[i](no answer)[/i]"
    console.print(Panel(Markdown(body), title="Answer", border_style="green"))


def _ensure_api_key() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.stderr.write(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.\n"
        )
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wiki-eval")
    sub = parser.add_subparsers(dest="cmd")

    p_ask = sub.add_parser("ask", help="Ask the agent a single question.")
    p_ask.add_argument("question", help="The question to answer.")
    p_ask.add_argument("--prompt", default="v1", help="Prompt version (default: v1).")
    p_ask.add_argument("--json", action="store_true", help="Emit run as JSON.")

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run the curated demo question set instead of taking a question.",
    )
    parser.add_argument(
        "--prompt",
        default="v1",
        help="Prompt version for --demo mode (default: v1).",
    )

    args = parser.parse_args(argv)
    _ensure_api_key()
    console = Console()

    if args.demo:
        for i, q in enumerate(DEMO_QUESTIONS, 1):
            console.rule(f"[bold]Demo {i}/{len(DEMO_QUESTIONS)}[/bold]: {q}")
            run = run_agent(q, prompt_version=args.prompt)
            _print_run(console, run)
        return 0

    if args.cmd == "ask":
        run = run_agent(args.question, prompt_version=args.prompt)
        if args.json:
            print(json.dumps(run.to_dict(), indent=2, ensure_ascii=False))
        else:
            console.rule(f"[bold]{args.question}[/bold]")
            _print_run(console, run)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
