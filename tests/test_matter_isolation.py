"""Mandantentrennung (DSGVO): die Where-Klausel darf NIEMALS fail-open sein.

_build_where ist eine staticmethod -> testbar ohne Embedder/DB.
"""
from rag_agent import VectorDatabase

bw = VectorDatabase._build_where


def test_fail_closed_ohne_matter_und_scopes():
    # Kein Mandant + keine Scopes -> niemals alle Akten, sondern 0 Treffer.
    assert bw(None, []) == "1 = 0"
    assert bw(None, None) == "1 = 0"


def test_matter_filter_gesetzt():
    where = bw("2024-101", [])
    assert "matter_id = '2024-101'" in where
    assert "scope = 'mandat'" in where


def test_scopes_einzeln():
    assert bw(None, ["allgemein"]) == "scope = 'allgemein'"
    assert "rechtsprechung" in bw(None, ["rechtsprechung"])


def test_matter_plus_scopes_kombiniert():
    where = bw("2024-101", ["allgemein", "rechtsprechung"])
    assert "matter_id = '2024-101'" in where
    assert "scope = 'allgemein'" in where
    assert "scope = 'rechtsprechung'" in where
    assert " OR " in where


def test_unbekannte_scopes_werden_ignoriert():
    # Nur whitelisted Scopes; 'mandat' als Scope-Eingabe zählt NICHT als Freigabe.
    assert bw(None, ["mandat", "hack", ""]) == "1 = 0"


def test_sql_escaping_einfache_anfuehrungszeichen():
    where = bw("a'b", [])
    # darf nicht mit unmaskiertem ' aus der String-Literal-Grenze ausbrechen
    assert "a''b" in where or "a\\'b" in where
