"""API-Ebene via FastAPI TestClient.

@slow: importiert app.py und lädt dabei den Embedder einmal. Kein echter
AWS-Aufruf (llm.stream wird gemockt). Mit '-m "not slow"' überspringbar.
"""
import os
import pytest

pytestmark = pytest.mark.slow

from fastapi.testclient import TestClient  # noqa: E402
import app as appmod  # noqa: E402
from pseudonymizer import ner_available  # noqa: E402

client = TestClient(appmod.app)
KEY = os.environ["ANWALT_API_KEY"]
H = {"X-API-Key": KEY}


def test_status_braucht_key():
    assert client.get("/api/status").status_code == 401


def test_version_endpoint_ohne_auth():
    """Version ist bewusst ohne Key abrufbar (Online-ist-neu?-Check vom Login aus)."""
    r = client.get("/api/version")
    assert r.status_code == 200
    import re as _re
    assert _re.match(r"^\d+\.\d+\.", r.json()["version"] + ".")  # x.y oder x.y.z


def test_admin_endpunkt_nicht_mit_kunden_key():
    # Ohne gesetzten ANWALT_ADMIN_KEY sind Admin-Endpunkte KOMPLETT zu –
    # insbesondere darf das Kunden-Passwort (API-Key) KEINE Logs öffnen.
    assert client.get("/api/logs", headers=H).status_code == 401
    assert client.get("/api/logs").status_code == 401


def test_status_mit_key_liefert_felder():
    r = client.get("/api/status", headers=H)
    assert r.status_code == 200
    data = r.json()
    for field in ("local_processing", "pseudonymize", "region", "embed_model",
                  "encryption_label", "matter_separation"):
        assert field in data


def test_models_liefert_default():
    r = client.get("/api/models", headers=H)
    assert r.status_code == 200
    data = r.json()
    assert data.get("default")
    assert isinstance(data.get("models"), list) and data["models"]


def test_document_nicht_gefunden_404():
    r = client.get("/api/document",
                   params={"source": "gibt-es-nicht.txt", "matter_id": "default"},
                   headers=H)
    assert r.status_code == 404


def test_ungueltige_matter_id_400():
    r = client.get("/api/document",
                   params={"source": "x.txt", "matter_id": "../etc/passwd"},
                   headers=H)
    assert r.status_code == 400


def test_usage_endpoint_liefert_verbrauch_und_budget():
    r = client.get("/api/usage", headers=H)
    assert r.status_code == 200
    d = r.json()
    assert "usage" in d and "budget" in d
    assert "available" in d["budget"]            # True (AWS-Budget) oder False+reason
    if d["usage"] is not None:
        assert "month_models" in d["usage"] and "month_cost_usd" in d["usage"]


def test_region_status_enthaelt_eu_modell_watcher():
    r = client.get("/api/region-status", headers=H)
    assert r.status_code == 200
    d = r.json()
    assert d["region"]
    assert isinstance(d.get("upcoming_eu_models"), list)   # [] ohne IAM-Recht ist ok


def test_translate_endpoint_gemockt(monkeypatch):
    monkeypatch.setattr(appmod.llm, "translate",
                        lambda text, lang, key: iter(["Übersetzt: ", text.upper()]))
    r = client.post("/api/translate", headers=H,
                    json={"text": "hello", "target_lang": "de"})
    assert r.status_code == 200
    assert "Übersetzt: HELLO" in r.text


def test_web_recherche_sendet_nur_pseudonymisierte_query(monkeypatch):
    """Nur die pseudonymisierte Anfrage darf an den Web-Suchdienst gehen."""
    if appmod.PSEUDONYMIZE and not ner_available():
        pytest.skip("ohne NER wird Web-Recherche bewusst übersprungen")

    captured = {}

    def fake_web_search(q, n=5):
        captured["query"] = q
        return []

    # Kein echter LLM-Aufruf
    monkeypatch.setattr(appmod.llm, "stream", lambda *a, **k: iter(["ok"]))
    monkeypatch.setattr(appmod.research, "web_search", fake_web_search)

    secret = "geheim.klient@example.com"
    r = client.post("/api/chat", headers=H, json={
        "query": f"Aktuelle Rechtsprechung für {secret}?",
        "matter_id": "default", "model": "claude-haiku-4.5", "web_research": True,
    })
    assert r.status_code == 200
    _ = r.text  # Stream konsumieren -> event_generator läuft
    assert "query" in captured, "web_search wurde nicht aufgerufen"
    assert secret not in captured["query"]      # E-Mail (Regex) darf NICHT durchgehen
