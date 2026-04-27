"""Discovery helper for the songs heldout eval set.

Same methodology as discover_movies.py: walk recent-songs categories on
Wikipedia, filter to articles created after 2025-09-01, dump article text
+ a structured summary so case authoring references real content.

Run: python eval/heldout/discover_songs.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "wiki-eval/0.1 songs-discovery"}
CUTOFF = datetime(2025, 9, 1, tzinfo=timezone.utc)
OUT_DIR = Path(__file__).parent / "_songs_corpus"

ROOT_CATS = [
    "2025 songs",
    "2026 songs",
    "2025 singles",
    "2026 singles",
]


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


def category_members(client: httpx.Client, category: str) -> list[dict]:
    members = []
    cmcontinue = None
    while True:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": 200,
            "cmtype": "page|subcat",
            "format": "json",
            "formatversion": "2",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue
        r = _backoff_get(client, WIKI_API, params=params)
        data = r.json()
        members.extend(data.get("query", {}).get("categorymembers", []))
        cmcontinue = data.get("continue", {}).get("cmcontinue")
        if not cmcontinue:
            break
        time.sleep(0.2)
    return members


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
    r = _backoff_get(client, WIKI_API, params=params)
    pages = r.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None
    revs = pages[0].get("revisions", [])
    if not revs:
        return None
    return datetime.fromisoformat(revs[0]["timestamp"].replace("Z", "+00:00"))


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


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []
    visited: set[str] = set()
    seen_titles: set[str] = set()
    with httpx.Client(headers=HEADERS) as client:
        to_visit: list[str] = list(ROOT_CATS)
        i = 0
        while i < len(to_visit):
            cat = to_visit[i]
            i += 1
            if cat in visited:
                continue
            visited.add(cat)
            print(f"# Category: {cat}", flush=True)
            try:
                members = category_members(client, cat)
            except httpx.HTTPError as e:
                print(f"  [skip] {e}", file=sys.stderr)
                continue
            for m in members:
                if m["ns"] == 14:
                    sub = m["title"].replace("Category:", "")
                    if sub not in visited and len(visited) < 25:
                        to_visit.append(sub)
                    continue
                if m["ns"] != 0:
                    continue
                title = m["title"]
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                time.sleep(0.25)
                try:
                    created = first_revision_ts(client, title)
                except httpx.HTTPError:
                    continue
                if not created or created < CUTOFF:
                    continue
                time.sleep(0.25)
                try:
                    article = fetch_extract(client, title)
                except httpx.HTTPError:
                    continue
                if "error" in article:
                    continue
                lead = (article["text"].split("\n\n")[0] if article["text"] else "")[:600]
                entry = {
                    "title": article["title"],
                    "created": created.isoformat()[:10],
                    "char_count": article["char_count"],
                    "lead": lead.replace("\n", " "),
                    "category": cat,
                }
                summary.append(entry)
                print(
                    f"  created={entry['created']}  chars={entry['char_count']:>6}  {title}"
                )
                fname = title.replace("/", "_").replace(":", "_")
                (OUT_DIR / f"{fname}.txt").write_text(article["text"])

    (OUT_DIR / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nDiscovered {len(summary)} post-cutoff song articles. Corpus in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
