# RAG-Architektur – Abgleich mit dem juristischen Blueprint

Ehrlicher Soll/Ist-Vergleich zwischen dem Referenz-Blueprint (5 Stufen) und der
tatsächlichen Implementierung. Kurzfazit: **Der Kern ist umgesetzt und empirisch
belegt (Hit@1 100 % auf dem Testset, siehe [analyse-paragraphen.md](analyse-paragraphen.md)). Drei
Feinheiten fehlen noch** (Parent-Child-Rekonstruktion, granulare Absatz-Metadaten,
Marker/LlamaParse-Parsing).

**Legende:** umgesetzt, teilweise, offen

---

## 1. Datenaufbereitung: Parsing & Chunking

| Blueprint | Status | Ist-Zustand |
|---|---|---|
| Parsing via **Marker/LlamaParse** → Markdown (Fußnoten, Tabellen, „Buchstabe aa") | teilweise | **Marker-Hook eingebaut** (lokal, via `ANWALT_PDF_PARSER=marker` + `pip install marker-pdf`), Fallback pypdf. **LlamaParse bewusst NICHT** (Cloud-API → Mandantsdaten an Dritte, DSGVO-Konflikt). |
| **Strukturbasiertes Chunking** (nie nach Zeichenlänge) | umgesetzt | `chunk_document` segmentiert an `Artikel N` / `§ N`. |
| **Parent-Child** (kleine Child-Chunks zum Suchen, voller Paragraf als Kontext) | umgesetzt | **Zweistufig:** Child = einzelner Absatz (Such-Ziel), Parent = voller Paragraf. Vor dem Prompt wird Child→Parent getauscht. |
| **Metadaten-Injektion** (`law_book`, `section`, `absatz`, `parent_id`) | umgesetzt | `section` („Artikel 59 – Datenschutz"), `absatz` („Abs. 2"), `law_book` („VO 1107/2009"), `parent_id` – hart in die Metadaten gebrannt + als Such-Präfix. |

---

## 2. Such-Strategie: Hybrid Search

| Blueprint | Status | Ist-Zustand |
|---|---|---|
| **Dense** (semantisch), Modell `bge-m3` / `cohere-v3` | umgesetzt | Vektorsuche mit **`BAAI/bge-m3`** (Standard, 1024-dim, mehrsprachig, 8k Kontext). |
| **Sparse/Lexical (BM25)** für exakte Ziffern/Begriffe | umgesetzt | Volltextindex (LanceDB FTS, BM25-artig) auf der text-Spalte. |
| **Fusion** beider Zweige | umgesetzt | Reciprocal Rank Fusion (RRF) in [retrieval.py](retrieval.py). |

---

## 3. Reranking

| Blueprint | Status | Ist-Zustand |
|---|---|---|
| Cross-Encoder **`BAAI/bge-reranker-v2-m3`** (lokal) / Cohere v3 (Cloud) | umgesetzt | **Exakt `BAAI/bge-reranker-v2-m3`** als Standard. Läuft auf GPU (ATOM) / CPU/MPS lokal. Relevanz-Score pro Treffer. |
| Top-20 → neu sortieren → Top 3–5 | umgesetzt | Kandidaten (≈25) → RRF → Cross-Encoder → Top-`k`. |

---

## 4. Rekonstruktion (Child → Parent vor dem Prompt)

| Blueprint | Status | Ist-Zustand |
|---|---|---|
| Treffer-Child gegen **vollständigen Parent-Paragraf** tauschen | umgesetzt | In `search()`: nach dem Reranking wird pro `parent_id` der beste Child behalten und sein `text` gegen den vollen `parent_text` getauscht. Das LLM erhält den ganzen Paragrafen. |

---

## 5. System-Prompt (Zitierpflicht / Anti-Halluzination)

| Blueprint-Regel | Status | Ist-Zustand |
|---|---|---|
| Nur aus bereitgestelltem Kontext antworten | umgesetzt | im `DEFAULT_SYSTEM_PROMPT` |
| Jede Behauptung mit exakter Norm zitieren | umgesetzt | „MUST reference that exact article/section … NEVER invent or guess" |
| Bei Unklarheit explizit „nicht beantwortbar" | umgesetzt | „state clearly that the information is not available" |
| Niemals Paragrafen/Aktenzeichen erfinden | umgesetzt | explizit verboten |
| Format „§ 28 Abs. 2 BDSG" (mit Absatz) | umgesetzt | Kontext liefert `Gesetz/Norm/Abs.`; der Prompt verlangt Zitate mit Gesetz+Artikel+Absatz (getestet: „Art. 59 Abs. 1 VO 1107/2009"). |

---

## Gesamtbewertung

**Der Blueprint ist vollständig umgesetzt:**
1. Strukturbasiertes Chunking (Artikel/§) – nie nach Zeichenlänge.
2. Parent-Child (Child = Absatz/Such-Ziel, Parent = voller Paragraf/Kontext).
3. Granulare Metadaten: `law_book`, `section`, `absatz`, `parent_id` (hart eingebrannt).
4. Hybrid-Suche: Dense (`bge-m3`) + Sparse/BM25 + RRF.
5. Cross-Encoder-Reranking (`bge-reranker-v2-m3`).
6. Child→Parent-Rekonstruktion vor dem Prompt.
7. Zitierpflicht-Prompt mit Absatz-Genauigkeit, Anti-Halluzination.

**Messbeleg:** Hit@1 100 % (vs. 20 % naiv) auf dem Testset ([analyse-paragraphen.md](analyse-paragraphen.md));
Beispiel-Zitat live: „Art. 59 Abs. 1 VO 1107/2009".

**Bewusste Abweichung:** **LlamaParse** wird *nicht* genutzt (Cloud-API →
Mandantsdaten an Dritte, DSGVO-/§-203-Konflikt). Stattdessen **Marker** lokal
(optional, `ANWALT_PDF_PARSER=marker`).

**Verbleibende Feinschliff-Optionen:** Satz-Ebene (`satz`) zusätzlich zur
Absatz-Ebene; Cohere Rerank v3 als Cloud-Alternative; automatische
`law_book`-Erkennung aus dem Dokument.
