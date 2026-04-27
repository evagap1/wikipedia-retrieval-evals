"""Discovery helper for the stubs/orphans eval set.

Strategy:

1. Probe MediaWiki search with ``srsort=create_timestamp_desc`` over several
   broad queries to surface RECENTLY CREATED articles.
2. For each candidate, fetch:
   - First revision timestamp (creation date) — keep only post-2025-09-01.
   - Article byte size — flag as STUB if < ~1500 bytes (excluding markup).
   - Incoming-link count from main namespace via ``prop=linkshere`` —
     flag as ORPHAN if the count is < 5.
3. Keep articles that are STUB OR ORPHAN. Save the article text so that
   eval-case authoring can reference real content.

Run: python eval/discover_stubs.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "wiki-eval/0.1 stubs-discovery"}
CUTOFF = datetime(2025, 9, 1, tzinfo=timezone.utc)
OUT_DIR = Path(__file__).parent / "_stubs_corpus"

# Broad probes — each finds many recently-created articles when combined with
# srsort=create_timestamp_desc. Mix of high-volume substrings and topical hooks.
PROBES = [
    "2025",
    "2026",
    "village",
    "footballer",
    "born",
    "species",
    "song",
    "election",
    "river",
    "school",
    "stub",
]

STUB_BYTE_LIMIT = 1500
ORPHAN_LINK_LIMIT = 5


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


def search_recent(client: httpx.Client, query: str, limit: int = 30) -> list[dict]:
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srsort": "create_timestamp_desc",
        "srprop": "snippet|timestamp|size",
        "format": "json",
        "formatversion": "2",
    }
    r = _backoff_get(client, WIKI_API, params=params)
    return r.json().get("query", {}).get("search", [])


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


def linkshere_count(client: httpx.Client, title: str, limit: int = 10) -> int:
    """Count incoming links from the main (article) namespace.

    Returns the number of incoming article-namespace links capped at ``limit``;
    if the result has ``limit`` entries, the true count may be higher (we
    only care that it's small for orphan detection).
    """
    params = {
        "action": "query",
        "prop": "linkshere",
        "titles": title,
        "lhprop": "title",
        "lhnamespace": "0",
        "lhlimit": limit,
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
    }
    r = _backoff_get(client, WIKI_API, params=params)
    pages = r.json().get("query", {}).get("pages", [])
    if not pages:
        return 0
    return len(pages[0].get("linkshere", []))


def fetch_article(client: httpx.Client, title: str, max_chars: int = 6000) -> dict:
    params = {
        "action": "query",
        "prop": "extracts|info",
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
    p = pages[0]
    text = (p.get("extract") or "")[:max_chars]
    return {
        "title": p.get("title", title),
        "pageid": p.get("pageid"),
        "size_bytes": p.get("length", 0),
        "text": text,
        "char_count": len(p.get("extract", "")),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []
    seen: set[str] = set()
    with httpx.Client(headers=HEADERS) as client:
        for probe in PROBES:
            print(f"\n# Probe: {probe!r}")
            try:
                hits = search_recent(client, probe, limit=30)
            except httpx.HTTPError as e:
                print(f"  [skip probe] {e}", file=sys.stderr)
                continue
            for h in hits:
                title = h["title"]
                if title in seen:
                    continue
                seen.add(title)
                time.sleep(0.25)
                try:
                    created = first_revision_ts(client, title)
                except httpx.HTTPError:
                    continue
                if not created or created < CUTOFF:
                    continue
                time.sleep(0.25)
                try:
                    article = fetch_article(client, title)
                except httpx.HTTPError:
                    continue
                if "error" in article:
                    continue
                size = article["size_bytes"] or h.get("size", 0)
                time.sleep(0.25)
                try:
                    links = linkshere_count(client, title, limit=ORPHAN_LINK_LIMIT + 1)
                except httpx.HTTPError:
                    continue
                is_stub = size and size < STUB_BYTE_LIMIT
                is_orphan = links < ORPHAN_LINK_LIMIT
                if not (is_stub or is_orphan):
                    continue
                entry = {
                    "title": article["title"],
                    "created": created.isoformat()[:10],
                    "size_bytes": size,
                    "incoming_links_main_ns": links,
                    "is_stub": bool(is_stub),
                    "is_orphan": bool(is_orphan),
                    "discovered_via": probe,
                    "lead": (article["text"][:600]).replace("\n", " "),
                }
                summary.append(entry)
                tag = []
                if is_stub:
                    tag.append("STUB")
                if is_orphan:
                    tag.append("ORPHAN")
                print(
                    f"  {','.join(tag):<13} created={entry['created']}  "
                    f"size={size:>5}  links={links}  {title}"
                )
                fname = title.replace("/", "_").replace(":", "_")
                (OUT_DIR / f"{fname}.txt").write_text(article["text"])

    (OUT_DIR / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nKept {len(summary)} candidates. Corpus saved to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
