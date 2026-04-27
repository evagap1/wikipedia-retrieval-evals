"""Discovery helper for the movies eval set.

For each candidate movie title, fetch the article and extract:
- the release date (parsed from the lead — looking for "released on ..." patterns)
- whether the article has Cast / Plot / Box office sections

Filter to movies with a release date ON OR AFTER 2025-09-01 — these are the
ones whose facts (cast, plot details, box office, reception) are post the
agent model's training cutoff and therefore truly require retrieval.

Run: python eval/discover_movies.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "wiki-eval/0.1 movies-discovery"}
RELEASE_CUTOFF = datetime(2025, 9, 1, tzinfo=timezone.utc)
OUT_DIR = Path(__file__).parent / "_movies_corpus"

CANDIDATES = [
    "One Battle After Another",
    "Frankenstein (2025 film)",
    "Bugonia (film)",
    "Wicked: For Good",
    "Avatar: Fire and Ash",
    "Zootopia 2",
    "The Smashing Machine (2025 film)",
    "Tron: Ares",
    "After the Hunt (2025 film)",
    "Predator: Badlands",
    "Sentimental Value",
    "Marty Supreme",
    "Now You See Me: Now You Don't",
    "Anaconda (2025 film)",
    "Scream 7",
    "Spider-Man: Brand New Day (film)",
    "The Super Mario Galaxy Movie",
    "Michael (2026 film)",
    "Caught Stealing (film)",
    "Eddington (film)",
    "The Long Walk (2025 film)",
    "Black Phone 2",
    "Roofman",
    "A House of Dynamite",
    "Wake Up Dead Man: A Knives Out Mystery",
    "The Bride! (film)",
    "Five Nights at Freddy's 2 (film)",
    "Springsteen: Deliver Me from Nowhere",
    "Mortal Kombat II (film)",
    "Sinners (2025 film)",
]

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"

# Match either "September 26, 2025" or "26 September 2025" anywhere in the lead.
DATE_PAT = re.compile(
    rf"(?:({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}}))|(?:(\d{{1,2}})\s+({MONTHS})\s+(\d{{4}}))"
)
MONTH_NUM = {m: i + 1 for i, m in enumerate(
    "January February March April May June July August September October November December".split()
)}


def _backoff_get(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    delay = 1.0
    for _ in range(5):
        r = client.get(url, params=params, timeout=20.0)
        if r.status_code == 429:
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        return r
    raise httpx.HTTPError("retries exhausted")


def fetch_extract(client: httpx.Client, title: str, max_chars: int = 12000) -> dict:
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "titles": title,
        "format": "json",
        "formatversion": "2",
    }
    r = _backoff_get(client, WIKI_API, params=params)
    pages = r.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return {"error": "missing"}
    page = pages[0]
    text = (page.get("extract") or "")[:max_chars]
    return {
        "title": page.get("title", title),
        "pageid": page.get("pageid"),
        "text": text,
        "char_count": len(page.get("extract", "")),
    }


def parse_release_date(lead_text: str) -> datetime | None:
    """Find the earliest plausible 'release date' in the lead paragraph.

    We assume the first concrete date in the lead is the theatrical release;
    most film articles open with "X is a YYYY film... released on [date]".
    Returns None if no date is found.
    """
    candidates: list[datetime] = []
    for m in DATE_PAT.finditer(lead_text):
        try:
            if m.group(1):  # "Month D, Y"
                month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
                dt = datetime(year, MONTH_NUM[month], day, tzinfo=timezone.utc)
            else:  # "D Month Y"
                day, month, year = int(m.group(4)), m.group(5), int(m.group(6))
                dt = datetime(year, MONTH_NUM[month], day, tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        if 2020 <= dt.year <= 2027:
            candidates.append(dt)
    if not candidates:
        return None
    # First plausible date in the lead is usually the principal release.
    return candidates[0]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []
    with httpx.Client(headers=HEADERS) as client:
        for title in CANDIDATES:
            time.sleep(0.4)
            try:
                article = fetch_extract(client, title)
            except httpx.HTTPError as e:
                print(f"[skip] {title}: {e}", file=sys.stderr)
                continue
            if "error" in article:
                print(f"[missing] {title}")
                continue
            text = article["text"]
            lead = text.split("\n\n")[0] if text else ""
            release = parse_release_date(lead) or parse_release_date(text[:3000])
            has_cast = "== Cast ==" in text or "\n=== Cast ===\n" in text
            has_plot = "== Plot ==" in text or "\n=== Plot ===\n" in text
            has_box_office = "Box office" in text or "box office" in text
            kept = release is not None and release >= RELEASE_CUTOFF
            entry = {
                "title": article["title"],
                "release_date": release.date().isoformat() if release else None,
                "post_sept_2025": kept,
                "has_cast_section": has_cast,
                "has_plot_section": has_plot,
                "has_box_office": has_box_office,
                "char_count": article["char_count"],
            }
            summary.append(entry)
            tag = "POST" if kept else "pre " if release else "??? "
            rd = release.date().isoformat() if release else "no-date"
            print(
                f"  {tag} {rd:<10} cast={'Y' if has_cast else 'n'} "
                f"plot={'Y' if has_plot else 'n'} bo={'Y' if has_box_office else 'n'} "
                f"len={article['char_count']:>6} {article['title']}"
            )
            (OUT_DIR / f"{article['title'].replace('/', '_').replace(':', '_')}.txt").write_text(text)

    (OUT_DIR / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    qualifying = [e for e in summary if e["post_sept_2025"] and e["has_cast_section"] and e["has_plot_section"]]
    print()
    print(f"=== Released ON OR AFTER 2025-09-01 with Cast+Plot ({len(qualifying)}) ===")
    for e in qualifying:
        print(f"  {e['release_date']}  {e['title']}  ({e['char_count']} chars, bo={e['has_box_office']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
