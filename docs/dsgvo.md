# DSGVO-Konformität – Begründung

Warum das System **Anwalt Agent RAG** datenschutzkonform betrieben werden kann.
Dieses Dokument ordnet die technischen Maßnahmen den DSGVO-Artikeln zu. Es ergänzt
die [tom.md](tom.md) (konkrete Maßnahmen) und ist als Begründungsteil für das
Verarbeitungsverzeichnis gedacht.

> Kein Rechtsrat. Finale Bewertung durch den/die Datenschutzbeauftragte(n).

---

## 1. Grundprinzip: Datenminimierung durch lokale Verarbeitung

Der gesamte sensible Verarbeitungsweg läuft **lokal** auf dem Kanzlei-Rechner bzw.
der ATOM:

- Einlesen, Bereinigen, **Chunking**, **Vektorisierung** (Embedding) und Speicherung
  (LanceDB) erfolgen ohne Netzwerkverbindung.
- Erst zur Antwortgenerierung werden **ausschließlich relevante Textauszüge** an das
  LLM übergeben – und diese sind zuvor **pseudonymisiert** (s. u.).

→ Erfüllt **Art. 5 Abs. 1 lit. c (Datenminimierung)** und **Art. 25 (Datenschutz
durch Technikgestaltung / privacy by design & by default)**.

---

## 2. Pseudonymisierung vor jedem Cloud-Versand (Art. 4 Nr. 5, Art. 32 Abs. 1 lit. a)

[pseudonymizer.py](pseudonymizer.py) ersetzt personenbezogene Daten **lokal** durch
Platzhalter, bevor irgendetwas die Maschine verlässt:

- Erkennung: Namen/Orte/Organisationen (spaCy-NER) + E-Mail, Telefon, IBAN,
  Steuer-ID, Datum, PLZ (Regex).
- **Reversibel, aber der Re-Identifizierungsschlüssel verlässt den Rechner nie.**
- Die gestreamte Antwort wird lokal zurückübersetzt; ein Safety-Net ersetzt vom
  LLM veränderte Platzhalter durch neutrale Labels (kein Roh-Token/kein Wert leakt).

→ Der **Empfänger (AWS) erhält keinen Personenbezug**. Damit ist die Übermittlung
datenschutzrechtlich erheblich entlastet (Erwägungsgrund 26). Bei Nutzung eines
**lokalen LLM** (ATOM) entfällt der Versand vollständig.

---

## 3. Vertraulichkeit, Integrität, Verfügbarkeit (Art. 32 Abs. 1 lit. b)

| Schutzziel | Maßnahme | Ort im Code |
|---|---|---|
| **Zugangskontrolle** | API-Key auf allen Endpunkten; Bind nur an 127.0.0.1; CORS auf feste Origins | [app.py](app.py) |
| **Zugriffskontrolle / Trennungsgebot** | `matter_id` pro Chunk; Suche/Statistik/Löschen aktenscharf gefiltert; Eingaben SQL-escaped | [rag_agent.py](rag_agent.py) |
| **Integrität / Rechenschaft** | Append-only Audit-Log (Ingest, Suche, Web-Recherche, Übersetzung, Transkription, Löschung) | [rag_agent.py](rag_agent.py) |
| **Eingabe-/Ausgabesicherheit** | Upload-Härtung (Allowlist, Größenlimit, Path-Traversal-Schutz); XSS-Schutz via DOMPurify | [app.py](app.py), [static/app.js](static/app.js) |
| **Verschlüsselung Transport** | TLS zu AWS Bedrock | – |
| **Verschlüsselung at rest** | **FileVault aktiv** (volle Festplattenverschlüsselung) | Betriebssystem |

→ Erfüllt **Art. 32 Abs. 1 (Sicherheit der Verarbeitung)**.

---

## 4. Betroffenenrechte

- **Art. 17 (Recht auf Löschung):** Löschen pro Dokument **und** pro Akte über
  Web-UI, API (`/api/delete`) und CLI (`delete`).
- **Art. 15 (Auskunft):** Bestand pro Akte abfragbar; Audit-Log dokumentiert Zugriffe.
- **Art. 5 Abs. 2 (Rechenschaftspflicht):** lückenloses Audit-Log.

---

## 5. Auftragsverarbeitung & Drittlandtransfer (Art. 28, Kap. V)

- **Region fest auf `eu-central-1` (Frankfurt).**
- **AVV:** AWS GDPR Data Processing Addendum (über AWS Artifact; Anleitung in
  [README.md](README.md)).
- **Compliance-Nachweise** des Auftragsverarbeiters: ISO 27001, BSI C5, SOC 2.
- **Kein Training auf Kundendaten:** AWS Bedrock verwendet Kundeninhalte nicht zum
  Modelltraining (vertraglich zugesichert) → das Kanzlei-Know-how wird nicht für
  Dritte nutzbar gemacht.
- **Restrisiko CLOUD-Act:** dokumentiert; gemindert durch Pseudonymisierung;
  vollständige Lösung = On-Prem-Betrieb auf der ATOM (kein Transfer mehr).

---

## 6. Berufsgeheimnis (§ 203 StGB / § 43e BRAO)

Über die DSGVO hinaus relevant für Kanzleien: Die Einschaltung von AWS als
Dienstleister ist nur zulässig mit **Mandanten-Einwilligung** bzw. auf Basis der
berufsrechtlichen Erlaubnis. Dies ist **organisatorisch** sicherzustellen
(Kanzlei-Dokument, kein technisches Thema) – siehe Maßnahmenplan in [tom.md](tom.md).

---

## 7. Zusammenfassung

| DSGVO-Anforderung | Status |
|---|---|
| Datenminimierung / Privacy by Design (Art. 5, 25) | erfuellt: lokal + nur Auszüge |
| Pseudonymisierung (Art. 4 Nr. 5, 32) | erfuellt: lokal, reversibel, Schlüssel bleibt lokal |
| Sicherheit der Verarbeitung (Art. 32) | erfuellt: Auth, Trennung, TLS, FileVault, Audit |
| Betroffenenrechte (Art. 15, 17) | erfuellt: Auskunft + Löschung pro Dokument/Akte |
| Rechenschaftspflicht (Art. 5 Abs. 2) | erfuellt: Audit-Log |
| Auftragsverarbeitung (Art. 28) | offen: AVV abzuschließen/zu dokumentieren (Anleitung vorhanden) |
| § 203 StGB Einwilligung | offen: organisatorisch durch Kanzlei |

Die technischen Voraussetzungen für einen DSGVO-konformen Betrieb sind erfüllt;
offen sind die **organisatorischen** Schritte (AVV dokumentieren, Einwilligungen,
Maßnahmenplan in [tom.md](tom.md)).
