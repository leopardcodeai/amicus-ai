"""LLMService.stream: die DSGVO-kritischen Pfade mit Fake-Provider (kein AWS).

Abgedeckt:
- Fail-closed: ohne NER geht KEINE Anfrage an ein Cloud-Modell raus
- Query wird pseudonymisiert, Antwort-Platzhalter werden über Chunk-Grenzen
  hinweg re-identifiziert
- Echte Token-Zahlen aus dem Provider landen im Verbrauchs-Ledger
"""
import re
import rag_agent
from rag_agent import LLMService


class FakeProvider:
    """Simuliert einen LLM-Provider: echot Platzhalter, meldet usage-Tokens."""
    def __init__(self, chunks_fn):
        self.chunks_fn = chunks_fn

    def stream(self, model_id, messages, system, usage_sink=None, **kw):
        if usage_sink is not None:
            usage_sink["input_tokens"] = 11
            usage_sink["output_tokens"] = 5
        yield from self.chunks_fn(messages)


def test_fail_closed_ohne_ner_bei_cloud_modell(monkeypatch):
    """Ohne Namens-Erkennung darf NIE ein Cloud-Aufruf passieren (Klarnamen-Leak)."""
    monkeypatch.setenv("ANWALT_REQUIRE_NER", "1")
    monkeypatch.setattr(rag_agent, "ner_available", lambda: False)

    def explode(_key):
        raise AssertionError("Provider wurde trotz fehlender NER aufgerufen!")
    monkeypatch.setattr(rag_agent, "get_provider", explode)

    svc = LLMService(pseudonymize=True)
    out = "".join(svc.stream("Frage zu Mandant Max Mustermann", [], "claude-haiku-4.5"))
    assert "Abgebrochen" in out
    assert "NER" in out or "Namens-Erkennung" in out


def test_pseudonymisierung_und_reidentifikation_ueber_chunks(monkeypatch):
    """E-Mail geht pseudonymisiert raus; der zurückkommende Platzhalter wird
    auch dann korrekt ersetzt, wenn er mitten im Token gesplittet ankommt."""
    monkeypatch.setattr(rag_agent, "ner_available", lambda: True)

    def chunks(messages):
        user = messages[0]["content"]
        m = re.search(r"\[\[EMAIL_\d+\]\]", user)
        assert m, f"Query war NICHT pseudonymisiert: {user[:100]!r}"
        assert "max@kanzlei.example" not in user, "Klartext-E-Mail ging an den Provider!"
        tok = m.group(0)
        yield "Antwort an " + tok[:6]          # Platzhalter absichtlich gesplittet
        yield tok[6:] + " wurde gesendet."
    monkeypatch.setattr(rag_agent, "get_provider", lambda k: (FakeProvider(chunks), "model-x"))

    svc = LLMService(pseudonymize=True)
    out = "".join(svc.stream("Bitte an max@kanzlei.example antworten", [], "claude-haiku-4.5"))
    assert "max@kanzlei.example" in out       # re-identifiziert
    assert "[[EMAIL" not in out               # kein Platzhalter übrig


def test_usage_tokens_werden_gebucht(monkeypatch):
    monkeypatch.setattr(rag_agent, "ner_available", lambda: True)
    monkeypatch.setattr(rag_agent, "get_provider",
                        lambda k: (FakeProvider(lambda m: iter(["ok"])), "model-x"))
    recorded = {}
    monkeypatch.setattr(rag_agent.usage, "record",
                        lambda key, i, o: recorded.update(key=key, inp=i, out=o))

    svc = LLMService(pseudonymize=False)
    "".join(svc.stream("Hallo", [], "claude-haiku-4.5"))
    assert recorded == {"key": "claude-haiku-4.5", "inp": 11, "out": 5}


def test_provider_fehler_wird_als_text_gemeldet(monkeypatch):
    """Ein Provider-Ausfall crasht den Stream nicht, sondern wird angezeigt."""
    monkeypatch.setattr(rag_agent, "ner_available", lambda: True)

    def broken(_key):
        raise RuntimeError("Verbindung weg")
    monkeypatch.setattr(rag_agent, "get_provider", broken)

    svc = LLMService(pseudonymize=False)
    out = "".join(svc.stream("Hallo", [], "claude-haiku-4.5"))
    assert "Fehler" in out and "Verbindung weg" in out
