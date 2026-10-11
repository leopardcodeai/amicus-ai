#!/usr/bin/env python3
"""
DSGVO-konforme Web-Recherche (Use-Case 3a).

Sicherheitsprinzip ("extra safer Weg"):
  1. Es wird NUR die (vom Aufrufer bereits pseudonymisierte) abstrakte Suchanfrage
     nach außen gegeben – niemals Mandantsdaten, Dokumentinhalte oder Klarnamen.
  2. Suche über eine datenschutzfreundliche Engine (DuckDuckGo HTML), die keine
     Nutzerprofile bildet und kein Tracking/keine API-Keys benötigt.
  3. Es werden nur Titel/URL/Snippet der Treffer zurückgegeben; diese fließen als
     klar gekennzeichneter externer Kontext in die Antwort ein (Quellen sichtbar).

So bleibt der Geheimnisschutz gewahrt, während aktuelle Rechtsprechung/Gesetze
recherchierbar sind.
"""

from __future__ import annotations
import os
import re
import html
import logging

logger = logging.getLogger("research")

# Whitelisted Such-API. Auf dem ATOM ist NUR der freigegebene Endpunkt erreichbar
# (Firewall), DuckDuckGo-Scraping dient nur dem lokalen Test.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")


def _tavily_search(query: str, max_results: int) -> list[dict]:
    """Datenschutzkonforme Suche über die whitelisted Tavily-API."""
    import httpx
    try:
        r = httpx.post("https://api.tavily.com/search", json={
            "api_key": TAVILY_API_KEY, "query": query, "max_results": max_results,
            "search_depth": "advanced",
        }, timeout=20.0)
        r.raise_for_status()
        return [{"title": x.get("title", ""), "url": x.get("url", ""),
                 "snippet": x.get("content", "")} for x in r.json().get("results", [])]
    except Exception as e:
        logger.warning("Tavily-Suche fehlgeschlagen: %s", e)
        return []

_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.DOTALL,
)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Führt eine datenschutzfreundliche Web-Suche aus. Gibt Treffer zurück.

    WICHTIG: `query` muss bereits pseudonymisiert/abstrakt sein – diese Funktion
    sendet sie unverändert an die Suchmaschine.
    """
    # Bevorzugt die whitelisted API (Produktivbetrieb auf dem ATOM)
    if TAVILY_API_KEY:
        hits = _tavily_search(query, max_results)
        if hits:
            return hits
    import httpx
    try:
        resp = httpx.post(
            _DDG_URL,
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (AnwaltAgent Research)"},
            timeout=15.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Web-Suche fehlgeschlagen: %s", e)
        return []

    titles = list(_RESULT_RE.finditer(resp.text))
    snippets = list(_SNIPPET_RE.finditer(resp.text))
    results = []
    for i, m in enumerate(titles[:max_results]):
        url = html.unescape(m.group("url"))
        # DuckDuckGo verpackt Ziel-URLs teils in uddg-Redirect – Original extrahieren
        redir = re.search(r"uddg=([^&]+)", url)
        if redir:
            from urllib.parse import unquote
            url = unquote(redir.group(1))
        snippet = _clean(snippets[i].group("snippet")) if i < len(snippets) else ""
        results.append({"title": _clean(m.group("title")), "url": url, "snippet": snippet})
    return results


def format_for_context(results: list[dict]) -> str:
    """Formatiert Treffer als externen Recherche-Kontext für das LLM."""
    if not results:
        return ""
    lines = ["EXTERNE WEB-RECHERCHE (öffentliche Quellen, bitte als solche kennzeichnen):"]
    for i, r in enumerate(results, 1):
        lines.append(f"[W{i}] {r.get('title','')}\n{r.get('url','')}\n{r.get('snippet','')}")
    return "\n\n".join(lines)
