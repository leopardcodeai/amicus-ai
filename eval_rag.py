#!/usr/bin/env python3
"""
RAG-Evaluation: naives Chunking vs. struktur-bewusstes Chunking + Hybrid/Reranker.

Belegt empirisch, warum die präzise Paragraphen-Zuordnung funktioniert. Nutzt die
EU-Verordnung 1107/2009 (Akte EU-1107-2009 muss ingestiert sein) als Goldstandard.

Aufruf:  uv run python eval_rag.py
"""
from __future__ import annotations
import re
import numpy as np
from rag_agent import Embedder, VectorDatabase, DocumentProcessor as D

REG = "beispieldaten/EU-1107-2009/VO_1107-2009_DE.txt"
MATTER = "EU-1107-2009"

# Testfälle: Frage -> erwartete Artikelnummer (Ground Truth aus dem Verordnungstext)
CASES = [
    ("Wo geht es um Datenschutz in der Verordnung?", 59),
    ("Vertrauliche Behandlung von Informationen", 63),
    ("Notfallsituationen im Pflanzenschutz / Notfallzulassung", 53),
    ("Parallelhandel mit Pflanzenschutzmitteln", 52),
    ("Gegenseitige Anerkennung von Zulassungen", 40),
    ("Genehmigungskriterien für Wirkstoffe", 4),
    ("Begriffsbestimmungen und Definitionen", 3),
    ("Werbung für Pflanzenschutzmittel", 66),
    ("Kennzeichnung von Pflanzenschutzmitteln", 65),
    ("Aufzeichnungen und Buchführungspflichten", 67),
]


def article_spans(text):
    """Liste (nummer, start, end) aller Artikel im Text."""
    ms = list(D._HEADING_RE.finditer(text))
    spans = []
    for i, m in enumerate(ms):
        num = re.search(r"\d+", m.group(1))
        if not num:
            continue
        start = m.start()
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        spans.append((int(num.group()), start, end))
    return spans


def naive_chunks_with_offset(text, size=800, overlap=150):
    """Zeichenbasiertes Sliding-Window MIT Start-Offset (für Artikel-Mapping)."""
    out, i = [], 0
    while i < len(text):
        out.append((text[i:i + size], i))
        i += size - overlap
    return out


def article_of(offset, spans):
    for num, s, e in spans:
        if s <= offset < e:
            return num
    return None


def cosine_topk(qvec, mat, k):
    sims = mat @ np.array(qvec)
    return np.argsort(-sims)[:k]


def main():
    text = D.clean_text(open(REG, encoding="utf-8").read())
    spans = article_spans(text)
    emb = Embedder()
    db = VectorDatabase(emb)

    # --- Naive Baseline aufbauen (in-memory) ---
    nchunks = naive_chunks_with_offset(text)
    nvecs = np.array(emb.embed_texts([c for c, _ in nchunks]))
    noffsets = [o for _, o in nchunks]

    print(f"Naive Chunks: {len(nchunks)} | Artikel im Text: {len(spans)}\n")
    print(f"{'Frage':52} | erw. | naiv@1 | naiv-Heading | struct@1 | struct-Top")
    print("-" * 120)

    naive_hit1 = naive_hit3 = naive_head = struct_hit1 = struct_hit3 = 0
    for q, exp in CASES:
        # Naiv
        qv = emb.embed_query(q)
        idx = cosine_topk(qv, nvecs, 3)
        naive_arts = [article_of(noffsets[i], spans) for i in idx]
        n1 = naive_arts[0] == exp
        n3 = exp in naive_arts
        # Enthält der Top-1-Chunk überhaupt eine Artikel-Überschrift?
        top_text = nchunks[idx[0]][0]
        head_present = bool(re.search(rf"Artikel\s+{exp}\b", top_text))
        naive_hit1 += n1
        naive_hit3 += n3
        naive_head += head_present

        # Strukturiert + Hybrid + Reranker
        res = db.search(q, limit=3, matter_id=MATTER)
        def artnum(r):
            m = re.search(r"Artikel\s+(\d+)", r.get("section", ""))
            return int(m.group(1)) if m else None
        sarts = [artnum(r) for r in res]
        # Leere DB/keine Treffer = Miss, kein Crash (CI-Umgebung ist frisch)
        s1 = bool(sarts) and sarts[0] == exp
        s3 = exp in sarts
        struct_hit1 += s1
        struct_hit3 += s3

        print(f"{q[:52]:52} | {exp:4} | {'Treffer' if n1 else 'daneben':8} | "
              f"{'ja' if head_present else 'NEIN':12} | {'Treffer' if s1 else 'daneben':8} | "
              f"{res[0].get('section','?')[:34] if res else '-'}")

    n = len(CASES)
    print("-" * 120)
    print(f"\nERGEBNIS (n={n}):")
    print(f"  Naiv (Vektor)         : Hit@1 {naive_hit1}/{n} ({100*naive_hit1//n}%) | Hit@3 {naive_hit3}/{n} | Top-Chunk enthält erwartete Artikel-Überschrift: {naive_head}/{n}")
    print(f"  Struktur+Hybrid+Rerank: Hit@1 {struct_hit1}/{n} ({100*struct_hit1//n}%) | Hit@3 {struct_hit3}/{n}")

    # CI-Gate (Nightly-Eval): fällt die Retrieval-Qualität unter die Schwelle,
    # schlägt der Job fehl -> Regression wird gemeldet, bevor der Kunde sie merkt.
    import os
    min_hit1 = int(os.environ.get("ANWALT_EVAL_MIN_HIT1_PCT", "0"))
    pct = 100 * struct_hit1 // n
    if min_hit1 and pct < min_hit1:
        print(f"\nHit@1 {pct}% < Schwelle {min_hit1}% -> Retrieval-Regression!")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
