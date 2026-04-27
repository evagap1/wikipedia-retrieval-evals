"""Extract Cast / Plot / Box office / Reception sections from each post-Sept-2025
movie corpus file, to make question authoring tractable.

Run: python eval/extract_movie_facts.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CORPUS = Path(__file__).parent / "_movies_corpus"
OUT = Path(__file__).parent / "_movies_facts.md"
SUMMARY = CORPUS / "_summary.json"

WANTED_SUBSTRINGS = ("cast", "plot", "box office", "release", "reception", "critical")


def extract_sections(text: str) -> list[tuple[str, str]]:
    """Yield (heading, body) pairs. The lead is returned as ('_lead_', text)."""
    parts = re.split(r"\n(==+ [^=\n]+ ==+)\n", text)
    out: list[tuple[str, str]] = [("_lead_", parts[0].strip())]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip(" =")
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        out.append((heading, body))
    return out


def main() -> int:
    summary = json.loads(SUMMARY.read_text())
    qualifying_titles = [
        e["title"] for e in summary if e.get("post_sept_2025") and e.get("has_cast_section") and e.get("has_plot_section")
    ]
    out_lines: list[str] = []
    for title in qualifying_titles:
        path = CORPUS / f"{title.replace('/', '_').replace(':', '_')}.txt"
        if not path.exists():
            continue
        text = path.read_text()
        sections = extract_sections(text)
        out_lines.append(f"\n# {title}\n")
        for heading, body in sections:
            if heading == "_lead_":
                out_lines.append(f"## Lead\n\n{body[:1500]}\n")
                continue
            lname = heading.lower()
            if any(w in lname for w in WANTED_SUBSTRINGS):
                trimmed = body[:2200]
                out_lines.append(f"## {heading}\n\n{trimmed}\n")
    OUT.write_text("\n".join(out_lines))
    print(f"Wrote {OUT}  ({len(qualifying_titles)} films)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
