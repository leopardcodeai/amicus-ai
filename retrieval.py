#!/usr/bin/env python3
"""
Hybrid-Retrieval + Cross-Encoder-Reranking (Ausbaustufe für Präzision).

Pipeline:
  1. Vektorsuche (semantisch)  -> Kandidaten
  2. Volltextsuche (BM25/FTS, exakte Begriffe wie "Artikel 59") -> Kandidaten
  3. Reciprocal Rank Fusion (RRF) der beiden Trefferlisten
  4. Cross-Encoder-Reranker bewertet jeden Kandidaten gegen die Frage paarweise
     und sortiert final -> Top-k

Warum: Reine Vektorsuche verwechselt manchmal thematisch ähnliche Normen.
Die Volltextsuche fängt exakte Begriffe/Normnummern, der Cross-Encoder beurteilt
Relevanz deutlich genauer als reine Embedding-Ähnlichkeit (er sieht Frage und
Chunk gemeinsam). Das erhöht die Präzision der zitierten Paragraphen.
"""

from __future__ import annotations
import os
import logging

logger = logging.getLogger("retrieval")

# bge-reranker-v2-m3: starker mehrsprachiger Cross-Encoder, sehr gut auf
# deutschen Rechtstexten (unterscheidet z.B. "Datenschutz" Art.59 von
# "Vertraulichkeit" Art.63). Auf der ATOM (GB10) läuft er auf der GPU.
RERANK_MODEL = os.environ.get("ANWALT_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RETRIEVAL_MODE = os.environ.get("ANWALT_RETRIEVAL_MODE", "hybrid")  # hybrid | vector

_RERANKER = None
_TRIED = False


def _device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def reranker_available() -> bool:
    return _get_reranker() is not None


def _get_reranker():
    global _RERANKER, _TRIED
    if _TRIED:
        return _RERANKER
    _TRIED = True
    try:
        from sentence_transformers import CrossEncoder
        _RERANKER = CrossEncoder(RERANK_MODEL, device=_device(), max_length=512)
        logger.info("Reranker aktiv: %s (%s)", RERANK_MODEL, _device())
    except Exception as e:
        logger.warning("Reranker nicht verfügbar (%s) – nutze RRF-Reihenfolge.", e)
        _RERANKER = None
    return _RERANKER


def rrf_merge(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion: kombiniert mehrere Ranglisten (Listen von IDs)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def rerank(query: str, candidates: list[dict], top_k: int, text_key: str = "text") -> list[dict]:
    """Sortiert Kandidaten per Cross-Encoder nach Relevanz zur Frage."""
    if not candidates:
        return []
    model = _get_reranker()
    if model is None:
        return candidates[:top_k]
    pairs = [(query, c.get(text_key, "")) for c in candidates]
    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.warning("Rerank-Fehler (%s) – Reihenfolge unverändert.", e)
        return candidates[:top_k]
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    return sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)[:top_k]
