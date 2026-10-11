"""DSGVO-kritischster Pfad: Pseudonymisierung & Re-Identifizierung.

NER (spaCy) wird bewusst deaktiviert (use_ner=False), damit die Tests
deterministisch nur die Regex-Schicht prüfen und ohne Modell laufen.
"""
import pytest
from pseudonymizer import Pseudonymizer, ner_available


def _p():
    return Pseudonymizer(use_ner=False)


# ---------- Reversibilität ----------

@pytest.mark.parametrize("text", [
    "Kontakt: max.mustermann@example.com",
    "IBAN DE89 3704 0044 0532 0130 00 bitte prüfen",
    "Tel. +49 89 1234567 erreichbar",
    "Frist 14.07.2026 beachten",
    "Anschrift: Hauptstr. 1, 50667 Köln",
])
def test_roundtrip_reversibel(text):
    p = _p()
    pseudo = p.pseudonymize(text)
    assert pseudo != text                      # es wurde etwas ersetzt
    assert p.reidentify(pseudo) == text        # exakt wiederherstellbar


# ---------- Kein Klartext-Leak ----------

def test_kein_pii_leak_im_pseudonymisat():
    p = _p()
    secret_mail = "sabine.hoffmann@kanzlei.de"
    secret_iban = "DE89370400440532013000"
    out = p.pseudonymize(f"Mail {secret_mail}, IBAN {secret_iban}")
    assert secret_mail not in out
    assert secret_iban not in out
    assert "[[EMAIL_" in out and "[[IBAN_" in out


# ---------- Regex-Präzision: juristische Zahlen NICHT zerstören ----------

@pytest.mark.parametrize("text", [
    "Streitwert 25000 Liter",
    "Zulassungsnummer 34567",
    "Randnummer 54321",
    "im Jahr 2026",
])
def test_keine_over_pseudonymisierung_von_fachzahlen(text):
    assert _p().pseudonymize(text) == text


# ---------- PLZ nur mit Kontext ----------

def test_plz_nur_mit_kontext():
    p = _p()
    assert "[[PLZ_" in p.pseudonymize("Sitz in D-40213 Musterstadt")
    assert "[[PLZ_" in p.pseudonymize("Musterweg 1, 50667 Köln")
    # nackte 5-stellige Zahl ohne Adresskontext bleibt erhalten
    assert _p().pseudonymize("Charge 12345 verfügbar") == "Charge 12345 verfügbar"


# ---------- Telefon-Formate ----------

@pytest.mark.parametrize("phone", [
    "+49 89 1234567",
    "(089) 12 34 56",
    "0049 30 1234567",
    "089/1234567",
])
def test_telefon_formate_erkannt(phone):
    out = _p().pseudonymize(f"Rückruf unter {phone}.")
    assert "[[TELEFON_" in out


# ---------- Steuer-ID: nur kanonische Form, keine 11-Ziffern-Kollision ----------

def test_steuerid_nur_kanonisch():
    p = _p()
    assert "[[STEUERID_" in p.pseudonymize("Steuer-IdNr 12 345 678 901")
    # 11 zusammenhängende Ziffern (z.B. Registriernummer) sind KEINE Steuer-ID
    assert _p().pseudonymize("Registriernummer 12345678901") == "Registriernummer 12345678901"


# ---------- Streaming-Re-Identifizierung über Chunk-Grenzen ----------

def test_stream_reidentifier_split_token():
    p = _p()
    p.pseudonymize("Mail a@b.de")              # erzeugt Mapping [[EMAIL_1]] -> a@b.de
    feed, flush = p.stream_reidentifier()
    # Token absichtlich mitten in zwei Chunks gesplittet
    part1, part2 = "Antwort: [[EMA", "IL_1]] Ende"
    out = feed(part1) + feed(part2) + flush()
    assert "a@b.de" in out
    assert "[[EMAIL" not in out


def test_stream_reidentifier_unbekanntes_token_wird_neutralisiert():
    p = _p()
    feed, flush = p.stream_reidentifier()
    out = feed("Siehe [[PERSON_9]] dort") + flush()
    # vom LLM erfundenes/verändertes Token -> neutrales Label, kein Leak, kein Roh-Token
    assert "[[PERSON_9]]" not in out
    assert "[Person]" in out


def test_ner_available_liefert_bool():
    assert isinstance(ner_available(), bool)
