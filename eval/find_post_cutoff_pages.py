"""Helper used while building the eval set.

Two strategies for finding popular articles created after 2025-09-01:

1. Probe a hand-picked candidate list and check first-revision timestamps.
2. Use MediaWiki search with ``srsort=create_timestamp_desc`` over broad
   recent-event keywords — this finds NEWLY CREATED articles on a topic.

For each post-cutoff article we then fetch pageview totals and rank.

Run: python eval/find_post_cutoff_pages.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS_API = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
    "/en.wikipedia/all-access/all-agents/{title}/daily/{start}/{end}"
)
HEADERS = {"User-Agent": "wiki-eval/0.1 eval-builder"}
CUTOFF = datetime(2025, 9, 1, tzinfo=timezone.utc)

CANDIDATES = [
    # Hurricanes / cyclones — confirmed post-cutoff article CREATIONS
    "Hurricane Melissa (2025)",
    "Cyclone Narelle (2026)",
    "Cyclone Fina",
    "Cyclone Maila",
    "Cyclone Vaianu (2026)",
    "Typhoon Halong (2025)",
    "Typhoon Bualoi (2025)",
    "Typhoon Kalmaegi (2025)",
    "Typhoon Fung-wong (2025)",
    # Awards / culture
    "62nd Baeksang Arts Awards",
    "2026 Rock League season",
    # Geopolitics
    "2026 Israel–Lebanon ceasefire",
    "Pakistan in the 2026 Iran war",
    # Sports / people
    "Ted Scott (caddie)",
    "Latasha Lattimore",
    "2026 Tour of Hainan",
    # Curiosities
    "Wombat feces",
    "Kicking Away the Ladder",
    # Specific 2025 Nobel laureates that may have new dedicated articles
    "Mary E. Brunkow",
    "Fred Ramsdell",
    "John Clarke (physicist)",
    "Michel Devoret",
    "John M. Martinis",
    "Susumu Kitagawa",
    "Richard Robson (chemist)",
    "Omar M. Yaghi",
    "László Krasznahorkai",
    "María Corina Machado",
    "Joel Mokyr",
    "Philippe Aghion",
    "Peter Howitt (economist)",
    # Specific 2025/26 events likely to have dedicated articles
    "2026 Bolivian general election",
    "2025 Madagascar protests",
    "2025 Nepal protests",
    "2025 Indonesia protests",
    "2025 in spaceflight",
    "2026 in spaceflight",
    "List of Wikipedia controversies",
]


def _request_with_backoff(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """GET with exponential backoff on 429 / transient errors."""
    delay = 1.0
    for attempt in range(5):
        try:
            r = client.get(url, params=params, timeout=20.0)
            if r.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        except httpx.HTTPError:
            if attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2
    raise httpx.HTTPError("retries exhausted")


def first_revision_ts(client: httpx.Client, title: str) -> datetime | None:
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": title,
        "rvlimit": 1,
        "rvdir": "newer",
        "rvprop": "timestamp",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }
    r = _request_with_backoff(client, WIKI_API, params=params)
    pages = r.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    revs = pages[0].get("revisions", [])
    if not revs:
        return None
    return datetime.fromisoformat(revs[0]["timestamp"].replace("Z", "+00:00"))


def total_views(client: httpx.Client, title: str, start: str, end: str) -> int | None:
    url = PAGEVIEWS_API.format(
        title=title.replace(" ", "_"), start=start, end=end
    )
    try:
        r = _request_with_backoff(client, url)
    except httpx.HTTPError:
        return None
    items = r.json().get("items", [])
    return sum(it.get("views", 0) for it in items)


SEARCH_PROBES = [
    "2025 Nobel Prize",
    "Hurricane 2025",
    "Typhoon 2025",
    "2026 election",
    "2025 election",
    "2026 in",
    "2025 film",
    "2026 film",
    "COP30",
    "Madagascar protests",
    "2025 protests",
    "death October 2025",
    "death November 2025",
    "death December 2025",
    "2025 World Series",
    "Super Bowl LX",
    "UEFA 2025",
    "2026 Winter Olympics",
]


def search_recent_creations(
    client: httpx.Client, query: str, limit: int = 20
) -> list[dict]:
    """Search Wikipedia and sort by creation date descending."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srsort": "create_timestamp_desc",
        "srprop": "timestamp",
        "format": "json",
        "formatversion": "2",
    }
    r = client.get(WIKI_API, params=params, timeout=20.0)
    r.raise_for_status()
    return r.json().get("query", {}).get("search", [])


def main() -> int:
    start = "20250901"
    end = "20260420"
    results = []
    seen_titles: set[str] = set()
    with httpx.Client(headers=HEADERS) as client:
        # Verify hand-picked candidates with delays to avoid rate limits
        print("=== Verifying hand-picked candidates ===\n")
        for title in CANDIDATES:
            if title in seen_titles:
                continue
            seen_titles.add(title)
            time.sleep(0.3)
            try:
                created = first_revision_ts(client, title)
            except httpx.HTTPError as e:
                print(f"[skip] {title}: {e}", file=sys.stderr)
                continue
            if created is None:
                print(f"[missing] {title}")
                continue
            post_cutoff = created >= CUTOFF
            views = total_views(client, title, start, end) if post_cutoff else None
            results.append(
                {
                    "title": title,
                    "created": created.isoformat(),
                    "post_cutoff": post_cutoff,
                    "views_sep2025_to_apr2026": views,
                }
            )
            tag = "POST" if post_cutoff else "pre "
            views_str = f"  views={views}" if views is not None else ""
            print(f"  {tag}  {created.date()}  {title}{views_str}")

    qualifying = sorted(
        (r for r in results if r["post_cutoff"] and (r.get("views_sep2025_to_apr2026") or 0) > 1000),
        key=lambda r: r.get("views_sep2025_to_apr2026") or 0,
        reverse=True,
    )
    print(f"\n=== Post-cutoff articles ranked by pageviews ({len(qualifying)} with >1k views) ===")
    for r in qualifying[:60]:
        via = r.get("discovered_via", "candidate")
        print(
            f"  {r['views_sep2025_to_apr2026']:>10}  "
            f"{r['created'][:10]}  {r['title']:<60}  via={via}"
        )

    # Persist for reproducibility
    import json
    from pathlib import Path

    out = Path(__file__).parent / "post_cutoff_articles.json"
    out.write_text(json.dumps(qualifying, indent=2, ensure_ascii=False))
    print(f"\nSaved {len(qualifying)} qualifying articles to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
