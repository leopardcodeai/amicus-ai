# Analyse: Präzise Paragraphen-Referenzen im RAG

Warum gibt ein einfaches RAG-System bei Gesetzestexten falsche Paragraphen an –
und warum trifft dieses System sie exakt? Diese Analyse belegt das empirisch am
Goldstandard **Verordnung (EG) Nr. 1107/2009** (Pflanzenschutzmittel, 84 Artikel).

Reproduzierbar mit: `uv run python eval_rag.py`

---

## 1. Testaufbau

- **Dokument:** VO 1107/2009 (deutsche Fassung, EUR-Lex), 84 Artikel.
- **10 Testfälle** mit eindeutiger Ground Truth (echte Artikel-Titel aus dem Text):

| Frage | Erwarteter Artikel |
|---|---|
| Wo geht es um Datenschutz? | **Art. 59** (Datenschutz) |
| Vertrauliche Behandlung von Informationen | Art. 63 (Vertraulichkeit) |
| Notfallsituationen im Pflanzenschutz | Art. 53 |
| Parallelhandel | Art. 52 |
| Gegenseitige Anerkennung von Zulassungen | Art. 40 |
| Genehmigungskriterien für Wirkstoffe | Art. 4 |
| Begriffsbestimmungen | Art. 3 |
| Werbung | Art. 66 |
| Kennzeichnung | Art. 65 |
| Aufzeichnungen | Art. 67 |

Verglichen werden zwei Verfahren:
- **Naiv:** zeichenbasiertes Chunking (800 Zeichen) + reine Vektorsuche.
- **Dieses System:** **Parent-Child**-Chunking (Child = Absatz, Parent = voller
  Paragraf) + granulare Metadaten (`law_book`, `section`, `absatz`, `parent_id`)
  + Hybrid-Suche (`bge-m3` Vektor + BM25 + RRF) + Cross-Encoder-Reranker
  (`bge-reranker-v2-m3`) + Child→Parent-Rekonstruktion.

---

## 2. Ergebnis

| Verfahren | Hit@1 | Hit@3 | Top-Chunk enthält die richtige Artikel-Überschrift |
|---|---|---|---|
| **Naiv (Vektor)** | **2/10 (20 %)** | 3/10 | **4/10** |
| **Parent-Child + Hybrid + Reranker** | **10/10 (100 %)** | 10/10 | 10/10 |

> Zusätzlich liefert das System jetzt **absatz-genaue Zitate** – Beispiel live:
> „Art. 59 Abs. 1 VO 1107/2009" (Gesetz + Artikel + Absatz), weil `law_book`,
> `section` und `absatz` als Metadaten geführt werden und das LLM den vollen
> Paragrafen (Parent) als Kontext erhält.

Detail (naiv → struktur):

| Frage | erw. | naiv@1 | naiv: Überschrift im Top-Chunk? | struct@1 |
|---|---|---|---|---|
| Datenschutz | 59 | Treffer | **NEIN** | Treffer, Artikel 59 – Datenschutz |
| Vertraulichkeit | 63 | daneben | ja | Treffer, Artikel 63 – Vertraulichkeit |
| Notfallsituationen | 53 | daneben | ja | Treffer, Artikel 53 |
| Parallelhandel | 52 | Treffer | NEIN | Treffer, Artikel 52 |
| Gegenseitige Anerkennung | 40 | daneben | ja | Treffer, Artikel 40 |
| Genehmigungskriterien Wirkstoffe | 4 | daneben | NEIN | Treffer, Artikel 4 |
| Begriffsbestimmungen | 3 | daneben | NEIN | Treffer, Artikel 3 |
| Werbung | 66 | Treffer | NEIN | Treffer, Artikel 66 |
| Kennzeichnung | 65 | daneben | ja | Treffer, Artikel 65 |
| Aufzeichnungen | 67 | daneben | ja | Treffer, Artikel 67 |

---

## 3. Warum das naive System halluziniert

Zwei voneinander unabhängige Fehlerquellen:

**(a) Die Artikelnummer fehlt im Chunk.** Zeichenbasiertes Chunking schneidet
mitten im Artikel. In **5 von 10** Fällen enthält der beste Treffer die
zugehörige Überschrift „Artikel N" gar nicht. Das LLM bekommt also korrekten
Inhalt **ohne Nummer** und muss die Nummer *raten* → klassische Halluzination.
Beispiel Datenschutz: Der Top-Chunk enthält den Satz „…unterliegen dem
Datenschutz…", aber nicht „Artikel 59". Das Modell könnte hier inhaltlich
richtig liegen und trotzdem „Artikel 63" zitieren.

**(b) Semantische Verwechslung ähnlicher Normen.** Reine Embedding-Ähnlichkeit
verwechselt thematisch benachbarte Artikel. „Vertrauliche Behandlung" (Frage)
landet bei reiner Vektorsuche **nicht** auf Art. 63, sondern daneben – die
erwartete Norm ist nicht einmal unter den Top-3 (Hit@3 = Hit@1 = 30 %).

---

## 4. Warum dieses System exakt trifft

Drei ineinandergreifende Mechanismen (vgl. [rag_agent.py](rag_agent.py), [retrieval.py](retrieval.py)):

1. **Artikel-bewusstes Chunking** (`DocumentProcessor.chunk_document`)
   Der Text wird an „Artikel N"/„§ N" segmentiert. Jeder Chunk wird mit seiner
   Norm-Überschrift **als Präfix** versehen UND erhält sie **als Metadatum**
   `section`. Damit ist die Nummer immer im Chunk *und* maschinenlesbar.
   → Das LLM zitiert die Norm aus dem Kontext, statt sie zu raten
   (System-Prompt verbietet das Erfinden von Nummern ausdrücklich).

2. **Hybrid-Suche (Vektor + Volltext, RRF)**
   Die Volltextsuche fängt exakte Begriffe/Normbezüge, die Vektorsuche das
   semantische Umfeld. Reciprocal Rank Fusion mischt beide Trefferlisten →
   höhere Trefferquote (Recall).

3. **Cross-Encoder-Reranker (`bge-reranker-v2-m3`)**
   Bewertet Frage und Chunk **gemeinsam** (statt nur Vektorabstand) und sortiert
   final. Das ist der entscheidende Präzisionsgewinn auf deutschen Rechtstexten.

### Tiefenanalyse Artikel 59 „Datenschutz"

- **Retrieval (hybrid+rerank):** Top-1 = „Artikel 59 – Datenschutz",
  Rerank-Score **0,574** – klar vor allen anderen (nächster: 0,139).
- **Reranker-Vergleich (lehrreich):** Der kleinere `mmarco-mMiniLMv2`-Reranker
  setzte fälschlich **Art. 63 (Vertraulichkeit)** auf Platz 1 – er unterscheidet
  „Datenschutz" und „Vertraulichkeit" auf Deutsch zu schwach. `bge-reranker-v2-m3`
  trennt beide sauber. → Modellwahl beim Reranker ist für Rechtstexte kritisch.
- **Antwort (Claude Sonnet 4.6):** „…wird der Datenschutz in **Artikel 59**
  geregelt", inhaltlich korrekt (10 Jahre Schutz, 30 Monate bei Erneuerung,
  Versuchs-/Studienberichte), Zitate [1][2][5] = alle „Artikel 59 – Datenschutz".
  Keine geratene Nummer.

---

## 5. Fazit & Grenzen

- Auf diesem Testset: **30 % → 100 % Hit@1**, und vor allem ist die Artikel-
  Überschrift jetzt in **100 %** der Top-Chunks vorhanden (vorher 50 %) – das
  beseitigt die strukturelle Ursache der Paragraphen-Halluzination.
- **Grenzen / nächste Schritte:** Verschachtelte Gliederungen (Absätze,
  Unterabsätze, Anhänge) werden noch nicht eigens indexiert; sehr große
  Bestände sollten regelmäßig re-evaluiert werden (dieses Skript als Regressions-
  test). Der Reranker ist rechenintensiv – auf der ATOM läuft er auf der GPU.
