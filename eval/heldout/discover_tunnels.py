"""Discovery helper for the tunnels heldout eval set.

Same methodology as discover_bridges.py: walk Category:Tunnels completed in
2026 (and the country-level subcats Wikipedia uses), fetch each article, and
dump article text + a structured summary.

Run: python eval/heldout/discover_tunnels.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "wiki-eval/0.1 tunnels-discovery"}
OUT_DIR = Path(__file__).parent / "_tunnels_corpus"

ROOT_CATS = [
    "Tunnels completed in 2026",
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


TYPES = [
    "rail", "road", "highway", "metro", "subway", "underground", "pedestrian",
    "utility", "water", "sewer", "service", "vehicular", "twin-tube", "twin tube",
    "single-tube", "single tube", "bored", "cut-and-cover", "immersed", "submarine",
    "underwater", "mountain", "base tunnel",
]

LENGTH_PAT = re.compile(r"(\d{1,2}[,\d]*\.?\d*)\s*(metres|meters|m|km|kilometres|kilometers|miles|mi|feet|ft)\b")


def detect_types(text_lower: str) -> list[str]:
    return [t for t in TYPES if t in text_lower]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: list[dict] = []
    visited: set[str] = set()
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
                    if sub not in visited:
                        to_visit.append(sub)
                    continue
                if m["ns"] != 0:
                    continue
                title = m["title"]
                if any(s["title"] == title for s in summary):
                    continue
                time.sleep(0.3)
                try:
                    article = fetch_extract(client, title)
                except httpx.HTTPError as e:
                    print(f"  [skip-article] {title}: {e}", file=sys.stderr)
                    continue
                if "error" in article:
                    continue
                text = article["text"]
                lead = text.split("\n\n")[0] if text else ""
                lead_lower = lead.lower()
                types = detect_types(lead_lower)
                lens = LENGTH_PAT.findall(lead.replace(",", "")) if lead else []
                entry = {
                    "title": article["title"],
                    "char_count": article["char_count"],
                    "lead": lead[:500],
                    "types_detected": types,
                    "length_strings_in_lead": [f"{a} {b}" for a, b in lens][:6],
                    "category": cat,
                }
                summary.append(entry)
                print(f"  {article['char_count']:>6}  types={types[:3]}  {title}")
                fname = title.replace("/", "_").replace(":", "_")
                (OUT_DIR / f"{fname}.txt").write_text(text)

    (OUT_DIR / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nDiscovered {len(summary)} tunnel articles. Corpus in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
