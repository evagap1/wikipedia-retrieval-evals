"""Wikipedia tool implementations backed by the live MediaWiki API.

Two tools are exposed to the agent:

- ``search_wikipedia(query, limit)``: title + snippet search.
- ``fetch_wikipedia_article(title, max_chars)``: plaintext extract for one article.

The Anthropic tool schemas live alongside the implementations so the agent loop
imports both from a single place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "wiki-eval/0.1 (https://github.com/anthropics/claude-code; eval harness)"

# Article extracts can be large. We cap the slice we feed back to the model so a
# single fetch can't blow the context window.
DEFAULT_FETCH_CHARS = 4000
MAX_FETCH_CHARS = 12000


# ---------------------------------------------------------------------------
# Tool schemas (Anthropic tool-use format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_wikipedia",
        "description": (
            "Search English Wikipedia for articles relevant to a query. Returns up "
            "to `limit` candidate articles with their title and a short snippet. "
            "Use this to discover which articles might contain the answer; then "
            "call fetch_wikipedia_article to read the most promising ones in "
            "depth. Prefer focused queries (entity names, distinctive phrases) "
            "over long natural-language questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms. Keep concise and specific.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (1-10). Default 5.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_wikipedia_article",
        "description": (
            "Fetch the plaintext extract of a Wikipedia article by exact title. "
            "Use the title returned from search_wikipedia. Returns the lead "
            "section plus body, truncated to `max_chars` characters. If the "
            "title is a redirect, the canonical title is followed automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Exact article title from search results.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": (
                        f"Truncate the returned text to this many characters "
                        f"(default {DEFAULT_FETCH_CHARS}, max {MAX_FETCH_CHARS})."
                    ),
                    "minimum": 500,
                    "maximum": MAX_FETCH_CHARS,
                },
            },
            "required": ["title"],
        },
    },
]


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


@dataclass
class SearchHit:
    title: str
    snippet: str
    pageid: int


def _strip_html(s: str) -> str:
    # MediaWiki search snippets contain <span class="searchmatch">...</span> tags.
    return re.sub(r"<[^>]+>", "", s).replace("&quot;", '"').replace("&amp;", "&")


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(15.0),
    )


def search_wikipedia(query: str, limit: int = 5) -> dict[str, Any]:
    """Search Wikipedia. Returns ``{"hits": [...]}`` or ``{"error": "..."}``."""
    limit = max(1, min(int(limit or 5), 10))
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srprop": "snippet",
        "format": "json",
        "formatversion": "2",
    }
    try:
        with _client() as client:
            r = client.get(WIKI_API, params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        return {"error": f"Wikipedia API error: {e}"}

    raw_hits = data.get("query", {}).get("search", [])
    hits = [
        {
            "title": h["title"],
            "snippet": _strip_html(h.get("snippet", "")),
            "pageid": h["pageid"],
        }
        for h in raw_hits
    ]
    if not hits:
        return {"hits": [], "note": f"No results for query: {query!r}"}
    return {"hits": hits}


def fetch_wikipedia_article(
    title: str, max_chars: int = DEFAULT_FETCH_CHARS
) -> dict[str, Any]:
    """Fetch a plaintext article extract by title.

    Uses ``prop=extracts&explaintext=1`` so the model gets clean prose with no
    wiki markup. Redirects are followed.
    """
    max_chars = max(500, min(int(max_chars or DEFAULT_FETCH_CHARS), MAX_FETCH_CHARS))
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "redirects": "1",
        "titles": title,
        "format": "json",
        "formatversion": "2",
    }
    try:
        with _client() as client:
            r = client.get(WIKI_API, params=params)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        return {"error": f"Wikipedia API error: {e}"}

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return {"error": f"No page found for title: {title!r}"}

    page = pages[0]
    if page.get("missing"):
        return {"error": f"Article not found: {title!r}"}

    extract = page.get("extract", "")
    if not extract:
        return {"error": f"Article has no extract: {title!r}"}

    canonical_title = page.get("title", title)
    truncated = len(extract) > max_chars
    body = extract[:max_chars].rstrip()
    if truncated:
        body += "\n\n[...article truncated. Call fetch_wikipedia_article again with a higher max_chars if you need more.]"

    return {
        "title": canonical_title,
        "pageid": page.get("pageid"),
        "url": f"https://en.wikipedia.org/?curid={page.get('pageid')}",
        "text": body,
        "truncated": truncated,
        "char_count": len(extract),
    }


# ---------------------------------------------------------------------------
# Dispatcher used by the agent loop
# ---------------------------------------------------------------------------


def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route a tool call from the agent to the right implementation."""
    if name == "search_wikipedia":
        return search_wikipedia(
            query=arguments.get("query", ""),
            limit=arguments.get("limit", 5),
        )
    if name == "fetch_wikipedia_article":
        return fetch_wikipedia_article(
            title=arguments.get("title", ""),
            max_chars=arguments.get("max_chars", DEFAULT_FETCH_CHARS),
        )
    return {"error": f"Unknown tool: {name}"}
