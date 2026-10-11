"""get_document_text: Reihenfolge + Deduplizierung nach parent_id.

Die DB wird gestubt, damit der Test ohne Embedder/LanceDB läuft.
"""
from rag_agent import VectorDatabase, TABLE_NAME


class _FakeQuery:
    def __init__(self, rows, sink=None):
        self._rows = rows
        self._sink = sink
    def search(self):
        return self
    def where(self, w):
        if self._sink is not None:
            self._sink["where"] = w
        return self
    def limit(self, _n):
        return self
    def to_list(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows, sink=None):
        self._rows = rows
        self._sink = sink
    def open_table(self, _name):
        return _FakeQuery(self._rows, self._sink)


def _db_with(rows, has_table=True, sink=None):
    db = VectorDatabase.__new__(VectorDatabase)   # ohne __init__ -> kein Embedder
    db.db = _FakeDB(rows, sink)
    db.list_tables = lambda: [TABLE_NAME] if has_table else []
    return db


def test_where_erlaubt_eigene_akte_und_geteilte_scopes():
    sink = {}
    _db_with([], sink=sink).get_document_text("VO.txt", "2024-103")
    w = sink["where"]
    assert "source = 'VO.txt'" in w
    assert "matter_id = '2024-103'" in w
    # geteilte Kategorien müssen sichtbar sein (zitierte Normen/Urteile)
    assert "scope = 'allgemein'" in w
    assert "scope = 'rechtsprechung'" in w


def test_reihenfolge_und_dedupe():
    rows = [
        {"parent_id": "p1", "parent_text": "Erster Absatz", "source": "doc", "matter_id": "m"},
        {"parent_id": "p1", "parent_text": "Erster Absatz", "source": "doc", "matter_id": "m"},  # Dup
        {"parent_id": "p2", "parent_text": "Zweiter Absatz", "source": "doc", "matter_id": "m"},
    ]
    text = _db_with(rows).get_document_text("doc", "m")
    assert text == "Erster Absatz\n\nZweiter Absatz"


def test_fallback_auf_text_wenn_kein_parent_text():
    rows = [{"parent_id": "p1", "text": "Nur Chunk-Text", "source": "d", "matter_id": "m"}]
    assert _db_with(rows).get_document_text("d", "m") == "Nur Chunk-Text"


def test_leere_tabelle_liefert_leerstring():
    assert _db_with([], has_table=False).get_document_text("d", "m") == ""


def test_leere_treffer_liefert_leerstring():
    assert _db_with([]).get_document_text("d", "m") == ""
