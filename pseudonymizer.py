#!/usr/bin/env python3
"""
Lokale, reversible Pseudonymisierung personenbezogener Daten (DSGVO Art. 4 Nr. 5).

Ziel: Bevor Text-Chunks oder die Nutzerfrage an ein Cloud-LLM (AWS Bedrock)
gesendet werden, werden alle direkten Personenbezüge lokal durch neutrale
Platzhalter ersetzt (z. B. "Max Mustermann" -> "[[PERSON_1]]"). Die Zuordnung
(Re-Identifizierungsschlüssel) verlässt den Rechner NIEMALS. Die gestreamte
Antwort des LLM wird lokal wieder zurückübersetzt, sodass der Anwender die
echten Namen sieht, das LLM aber nur Pseudonyme verarbeitet hat.

Damit verlassen keine personenbezogenen Daten mehr die Maschine -> die
Übermittlung an AWS ist datenschutzrechtlich erheblich entlastet (Erwägungs-
grund 26 DSGVO: pseudonymisierte Daten bleiben zwar personenbezogen, solange
der Schlüssel existiert, aber der *Empfänger* AWS erhält keinen Personenbezug).

Erkennung:
  * Strukturierte Identifikatoren (deterministisch, hohe Präzision) via Regex:
    E-Mail, Telefon, IBAN, Steuer-IdNr, Datum, PLZ.
  * Namen / Orte / Organisationen via spaCy German NER (optional, best-effort).
    Ist spaCy bzw. das Modell nicht installiert, läuft die Regex-Schicht weiter
    und es wird einmalig gewarnt.
"""

import re
import logging

logger = logging.getLogger("pseudonymizer")

# --- Optionale NER-Schicht (spaCy) --------------------------------------------
_NLP = None
_NER_TRIED = False
_NER_LABELS = {"PER": "PERSON", "PERSON": "PERSON", "LOC": "ORT", "ORG": "ORG"}

# Häufige juristische Rollen-/Sachbegriffe, die NER fälschlich als Entität taggt.
# Diese werden NICHT pseudonymisiert (kein Personenbezug, erhält Lesbarkeit).
_NER_STOPWORDS = {
    "kläger", "klägerin", "beklagter", "beklagte", "antragsteller",
    "antragstellerin", "antragsgegner", "antragsgegnerin", "mandant",
    "mandantin", "kläger:in", "auftraggeber", "auftragnehmer", "sohn",
    "tochter", "witwe", "erblasser", "erbe", "miterbe", "kind", "kinder",
    "pkw", "kfz", "rechtsanwalt", "rechtsanwältin", "notar", "gericht",
    "parteien", "partei", "verkäufer", "käufer", "vermieter", "mieter",
}


def ner_available() -> bool:
    """True, wenn das deutsche NER-Modell geladen werden konnte."""
    return _get_nlp() is not None


def _get_nlp():
    """Lädt das deutsche spaCy-Modell lazy. Gibt None zurück, falls nicht da."""
    global _NLP, _NER_TRIED
    if _NER_TRIED:
        return _NLP
    _NER_TRIED = True
    try:
        import spacy
        for model in ("de_core_news_lg", "de_core_news_md", "de_core_news_sm"):
            try:
                _NLP = spacy.load(model, disable=["lemmatizer", "tagger", "parser"])
                logger.info("Pseudonymizer: spaCy-NER aktiv (%s)", model)
                break
            except Exception:
                continue
        if _NLP is None:
            logger.warning(
                "Pseudonymizer: kein deutsches spaCy-Modell gefunden. Namen werden "
                "NICHT per NER erkannt. Installieren mit: "
                "python -m spacy download de_core_news_md"
            )
    except ImportError:
        logger.warning(
            "Pseudonymizer: spaCy nicht installiert -> nur Regex-Erkennung aktiv."
        )
    return _NLP


# --- Strukturierte Muster --------------------------------------------------
# Geldbeträge werden BEWUSST NICHT pseudonymisiert, da sie für juristische
# Antworten relevant und i. d. R. nicht personenidentifizierend sind.
# Die Liste ist nach Priorität geordnet (oben = höchste Priorität). Strukturierte
# Regex-Treffer haben Vorrang vor NER, damit z. B. eine IBAN nicht von der
# Telefon-/Namens-Erkennung zerschnitten wird.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")),
    # Deutsche Steuer-IdNr: NUR die kanonische, mit Leerzeichen gruppierte Form
    # "12 345 678 901". Bewusst eng, damit nicht jede 11-stellige Zahl (Akten-/
    # Registriernummern) zerstört wird. Kontiguierte IdNr meist gelabelt -> NER/Doku.
    ("STEUERID", re.compile(r"\b\d{2} \d{3} \d{3} \d{3}\b")),
    # Telefon: +<Land>, 0049, (0XX), 0.. mit toleranten Trennern und >=2 Zifferngruppen
    # (so werden (089) 12 34 56 und internationale Nummern erfasst, kurze Zahlen nicht).
    ("TELEFON", re.compile(
        r"(?<!\w)(?:\+\d{1,3}|0049|\(0\d{1,5}\)|0)[\s/\-]*\d{2,5}(?:[\s/\-]*\d{2,9}){1,4}(?!\w)"
    )),
    # Datum: 01.02.2024 / 1.2.24 / 2024-02-01
    ("DATUM", re.compile(
        r"\b(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2})\b"
    )),
    # Deutsche PLZ: NUR mit Kontext ("D-12345" oder Adresszeile "..., 52441") –
    # eine nackte 5-stellige Zahl ist zu unspezifisch (Beträge, Aktenzeichen, Jahre).
    ("PLZ", re.compile(r"\bD-\d{5}\b|(?<=,\s)\d{5}\b")),
]
# Priorität (kleiner = wichtiger) für die Überlappungsauflösung.
_PRIORITY = {
    "EMAIL": 0, "IBAN": 1, "STEUERID": 2, "TELEFON": 3, "DATUM": 4,
    "PERSON": 5, "PLZ": 6, "ORT": 7, "ORG": 8,
}

# Platzhalter-Format: [[TYP_N]] – wird von LLMs zuverlässig unverändert
# durchgereicht und ist eindeutig per Regex zurückzuersetzen.
_TOKEN_RE = re.compile(r"\[\[[A-ZÄÖÜ]+_\d+\]\]")
# Mögliche angefangene Tokens am Stream-Ende (zum Zurückhalten beim Streaming).
# Das optionale schließende "]" fängt den Fall ab, dass das "]]" über zwei
# Chunks zerteilt wird (Puffer endet auf "[[PERSON_1]") – sonst leakt das
# unvollständige Token roh, weil _TOKEN_RE zwei "]" verlangt.
_PARTIAL_TOKEN_RE = re.compile(r"\[\[[A-ZÄÖÜ]*_?\d*\]?$|\[$")


class Pseudonymizer:
    """Reversibler Pseudonymisierer mit per-Vorgang konsistenter Zuordnung."""

    def __init__(self, use_ner: bool = True):
        self.use_ner = use_ner
        # value -> token  und  token -> value
        self._forward: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    # -- öffentliche API -------------------------------------------------------
    def pseudonymize(self, text: str) -> str:
        """Ersetzt erkannte PII durch Platzhalter. Aktualisiert die Map."""
        if not text:
            return text

        spans: list[tuple[int, int, str, str]] = []  # (start, end, type, value)

        # 1) NER (Namen/Orte/Orgs) – best effort
        nlp = _get_nlp() if self.use_ner else None
        if nlp is not None:
            try:
                doc = nlp(text)
                for ent in doc.ents:
                    typ = _NER_LABELS.get(ent.label_)
                    name = ent.text.strip()
                    if typ and len(name) > 1 and name.lower() not in _NER_STOPWORDS:
                        spans.append((ent.start_char, ent.end_char, typ, ent.text))
            except Exception as e:  # pragma: no cover
                logger.warning("NER-Fehler, überspringe: %s", e)

        # 2) Strukturierte Muster
        for typ, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                spans.append((m.start(), m.end(), typ, m.group()))

        if not spans:
            return text

        # Überlappungen auflösen: nach Priorität (strukturiert vor NER), dann
        # längster Treffer. Akzeptierte Spans dürfen sich nicht überschneiden.
        spans.sort(key=lambda s: (_PRIORITY.get(s[2], 99), -(s[1] - s[0])))
        chosen: list[tuple[int, int, str, str]] = []
        for span in spans:
            start, end = span[0], span[1]
            if not any(start < c[1] and end > c[0] for c in chosen):
                chosen.append(span)

        # Von hinten nach vorne ersetzen, damit die Indizes gültig bleiben.
        for start, end, typ, value in sorted(chosen, key=lambda s: s[0], reverse=True):
            token = self._token_for(typ, value)
            text = text[:start] + token + text[end:]

        # Zweiter Durchgang (Vollständigkeit): NER taggt eine Entität ggf. nur an
        # einzelnen Stellen. Damit kein bereits bekannter PII-Wert irgendwo im
        # Klartext zurückbleibt, werden alle Werte global literal ersetzt
        # (längste zuerst, um Teilersetzungen zu vermeiden).
        for value, token in sorted(
            self._forward_values(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if value and value in text:
                text = text.replace(value, token)

        return text

    def _forward_values(self):
        """Liefert (originalwert, token)-Paare aus der aktuellen Map."""
        return [(v, t) for t, v in self._reverse.items()]

    # Neutrale Labels, falls das LLM eine Platzhalter-Nummer verändert/erfunden
    # hat (Token-Drift). So leakt weder ein echter Wert noch ein interner Token.
    _GENERIC = {
        "PERSON": "[Person]", "ORT": "[Ort]", "ORG": "[Organisation]",
        "EMAIL": "[E-Mail]", "IBAN": "[IBAN]", "TELEFON": "[Telefon]",
        "DATUM": "[Datum]", "STEUERID": "[Steuer-ID]", "PLZ": "[PLZ]",
    }

    def reidentify(self, text: str) -> str:
        """Ersetzt Platzhalter durch Originalwerte. Unbekannte (vom LLM
        veränderte) Tokens werden durch ein neutrales Label ersetzt."""
        if not text:
            return text

        def _sub(m):
            tok = m.group()
            if tok in self._reverse:
                return self._reverse[tok]
            inner = tok.strip("[]")
            typ = inner.split("_")[0]
            return self._GENERIC.get(typ, "[geschwärzt]")

        return _TOKEN_RE.sub(_sub, text)

    def stream_reidentifier(self):
        """Erzeugt einen Streaming-fähigen Re-Identifizierer.

        Verwendung:
            feed, flush = pseudonymizer.stream_reidentifier()
            for chunk in llm_stream: yield feed(chunk)
            yield flush()

        Hält am Pufferende ggf. ein angefangenes Token zurück, damit über
        Chunk-Grenzen gesplittete Platzhalter korrekt ersetzt werden.
        """
        buffer = {"text": ""}

        def feed(chunk: str) -> str:
            buffer["text"] += chunk
            buf = buffer["text"]
            # Falls am Ende ein angefangenes Token steht: zurückhalten
            m = _PARTIAL_TOKEN_RE.search(buf)
            if m:
                emit, buffer["text"] = buf[: m.start()], buf[m.start():]
            else:
                emit, buffer["text"] = buf, ""
            return self.reidentify(emit)

        def flush() -> str:
            rest = buffer["text"]
            buffer["text"] = ""
            return self.reidentify(rest)

        return feed, flush

    @property
    def mapping(self) -> dict[str, str]:
        """token -> originalwert (Re-Identifizierungsschlüssel, bleibt lokal!)."""
        return dict(self._reverse)

    # -- intern ----------------------------------------------------------------
    def _token_for(self, typ: str, value: str) -> str:
        key = f"{typ}::{value.strip().lower()}"
        if key in self._forward:
            return self._forward[key]
        self._counters[typ] = self._counters.get(typ, 0) + 1
        token = f"[[{typ}_{self._counters[typ]}]]"
        self._forward[key] = token
        self._reverse[token] = value
        return token


if __name__ == "__main__":
    # Schneller Selbsttest
    logging.basicConfig(level=logging.INFO)
    p = Pseudonymizer()
    sample = (
        "Herr Max Mustermann (geb. 01.02.1980), wohnhaft in 80331 München, "
        "E-Mail max@example.com, Tel. +49 89 1234567, IBAN DE89 3704 0044 0532 0130 00, "
        "schloss am 15.03.2024 einen Vertrag."
    )
    ps = p.pseudonymize(sample)
    print("Pseudonymisiert:\n", ps)
    print("\nZurück:\n", p.reidentify(ps))
