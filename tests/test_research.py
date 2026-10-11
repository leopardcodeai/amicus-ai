"""Web-Recherche: robuste Kontext-Formatierung (kein Crash bei abweichenden Feldern)."""
import research


def test_format_for_context_leer():
    assert research.format_for_context([]) == ""


def test_format_for_context_vollstaendig():
    out = research.format_for_context([
        {"title": "EuGH Urteil", "url": "https://example.eu/1", "snippet": "Leitsatz ..."},
    ])
    assert "EuGH Urteil" in out
    assert "https://example.eu/1" in out
    assert "Leitsatz" in out


def test_format_for_context_fehlende_felder_kein_crash():
    # Defensive .get(): fehlende url/snippet dürfen den Stream nicht abbrechen.
    out = research.format_for_context([{"title": "Nur Titel"}])
    assert "Nur Titel" in out
