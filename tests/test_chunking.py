"""Parent-Child-Chunking: Struktur-Erkennung, Absatz-Split, parent_id-Konsistenz."""
from collections import defaultdict
from rag_agent import DocumentProcessor

REQUIRED_KEYS = {"text", "parent_text", "section", "absatz", "law_book", "parent_id"}

STRUCTURED = """Artikel 1
Erster Paragraf.
(1) Erster Absatz hier.
(2) Zweiter Absatz hier.
Artikel 2
Zweiter Paragraf mit Inhalt.
Artikel 3
Dritter Paragraf mit Inhalt.
"""


def test_chunks_haben_alle_pflichtfelder():
    chunks = DocumentProcessor.chunk_document(STRUCTURED, law_book="VO 1107/2009")
    assert chunks
    for c in chunks:
        assert REQUIRED_KEYS.issubset(c.keys())


def test_strukturierte_normen_tragen_section():
    chunks = DocumentProcessor.chunk_document(STRUCTURED, law_book="VO 1107/2009")
    sections = {c["section"] for c in chunks if c["section"]}
    assert any(s.startswith("Artikel 1") for s in sections)
    assert any(s.startswith("Artikel 2") for s in sections)


def test_parent_id_konsistenz_pro_artikel():
    chunks = DocumentProcessor.chunk_document(STRUCTURED)
    by_section = defaultdict(set)
    for c in chunks:
        if c["section"]:
            by_section[c["section"]].add(c["parent_id"])
    # Alle Kinder eines Artikels verweisen auf genau EINEN Parent.
    for section, pids in by_section.items():
        assert len(pids) == 1, f"{section} hat mehrere parent_ids: {pids}"


def test_absatz_split_erkannt():
    chunks = DocumentProcessor.chunk_document(STRUCTURED)
    absaetze = {c["absatz"] for c in chunks if c["absatz"]}
    assert "Abs. 1" in absaetze
    assert "Abs. 2" in absaetze


def test_unstrukturierter_text_jeder_chunk_eigener_parent():
    text = "Dies ist ein einfacher Mandantenvermerk ohne jede Normstruktur. " * 5
    chunks = DocumentProcessor.chunk_document(text)
    assert chunks
    for c in chunks:
        # eigener Parent: parent_text entspricht dem Chunk-Text selbst
        assert c["parent_text"] == c["text"]
        assert c["parent_id"]
        assert c["section"] == ""


def test_law_book_durchgereicht():
    chunks = DocumentProcessor.chunk_document(STRUCTURED, law_book="VO 1107/2009")
    assert all(c["law_book"] == "VO 1107/2009" for c in chunks)
