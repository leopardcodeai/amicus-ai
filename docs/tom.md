# Technische und Organisatorische Maßnahmen (TOM)
### gemäß Art. 32 DSGVO – Anwalt Agent RAG

> **Hinweis:** Dieses Dokument beschreibt die Maßnahmen zum Schutz personen­bezogener
> Daten im System „Anwalt Agent RAG". Es ist Bestandteil des Datenschutz­konzepts der
> Kanzlei und Anlage zum Verarbeitungsverzeichnis. Vor Inkraftsetzung durch den/die
> Datenschutzbeauftragte(n) zu prüfen und freizugeben.
> *Kein Rechtsrat – fachliche/rechtliche Freigabe durch DSB erforderlich.*

---

## 0. Stammdaten

| Feld | Eintrag |
|---|---|
| Verantwortlicher (Art. 4 Nr. 7) | _[Kanzleiname, Anschrift]_ |
| Datenschutzbeauftragte(r) | _[Name, Kontakt]_ |
| System / Verarbeitungstätigkeit | Anwalt Agent RAG – KI-gestützte Dokumentenanalyse |
| Verarbeitete Datenkategorien | Mandanten-/Verfahrensdokumente; vor Cloud-Versand **pseudonymisierte** Textauszüge |
| Auftragsverarbeiter | Amazon Web Services EMEA SARL, Region `eu-central-1` (Frankfurt) |
| Rechtsgrundlage AVV | AWS GDPR Data Processing Addendum (AWS Artifact) |
| Stand / Version | _[Datum]_ / v1.0 |

---

## 1. Vertraulichkeit (Art. 32 Abs. 1 lit. b)

### 1.1 Zutrittskontrolle (physisch)
- Verarbeitung ausschließlich auf dem Kanzlei-Rechner / der lokalen ATOM-Hardware.
- _[Serverraum/Arbeitsplatz zugangsgesichert; Bildschirmsperre aktiv]_ — **organisatorisch sicherzustellen**.

### 1.2 Zugangskontrolle (System)
- **API-Key-Authentifizierung** auf allen API-Endpunkten (Header `X-API-Key`); ohne gültigen Key → HTTP 401.
- Server bindet standardmäßig nur an `127.0.0.1` (kein Netzwerkzugriff von außen).
- **CORS** auf definierte Origins beschränkt (kein Wildcard).
- **Offen/organisatorisch:** Key-Rotation, sichere Aufbewahrung des Keys.

### 1.3 Zugriffskontrolle (Daten)
- **Mandanten-/Aktentrennung** über `matter_id`: Such-, Statistik- und Löschoperationen
  sind auf die jeweilige Akte gefiltert (Trennungsgebot). Kreuz-Lecks getestet/ausgeschlossen.
- Eingaben werden SQL-escaped und gegen ein Whitelist-Muster validiert.

### 1.4 Pseudonymisierung (Art. 32 Abs. 1 lit. a) — **Kernmaßnahme**
- Vor jedem Cloud-Versand werden personenbezogene Daten **lokal** durch Platzhalter ersetzt
  (Namen via NER, sowie E-Mail/Telefon/IBAN/Steuer-ID/Datum/PLZ via Regex).
- Der **Re-Identifizierungsschlüssel verlässt den Rechner nie**; die Cloud (AWS) erhält
  keinen Personenbezug.
- Zweiter globaler Durchgang stellt Vollständigkeit sicher; ein Safety-Net ersetzt vom
  LLM veränderte Platzhalter durch neutrale Labels (kein Roh-Token/kein Wert leakt).
- Abschaltbar nur per Konfiguration (`ANWALT_PSEUDONYMIZE`); im Echtbetrieb **aktiv**.

### 1.5 Eingabesicherheit (Anwendung)
- Upload: Endungs-Whitelist (`.pdf/.docx/.txt/.md`), Größenlimit, Path-Traversal-Schutz
  (UUID-Dateinamen statt Originalpfad).
- Frontend rendert Modell-/Dokumentinhalte nur über **DOMPurify**; alle dynamischen
  Werte HTML-escaped → Schutz gegen XSS.

---

## 2. Integrität (Art. 32 Abs. 1 lit. b)

- **Audit-Log** (append-only, `audit_log.jsonl`) protokolliert Ingest, Suche und Löschung
  mit Zeitstempel und Akten-ID → Nachvollziehbarkeit (Art. 5 Abs. 2 Rechenschaftspflicht).
- **Quellenangaben/Citations** zu jeder Antwort (Dokument + Seite) → Eingabe-/Ergebniskontrolle.
- **Offen/organisatorisch:** regelmäßige Sichtung des Audit-Logs, Schutz des Logs vor Manipulation.

---

## 3. Verfügbarkeit und Belastbarkeit (Art. 32 Abs. 1 lit. b, c)

- Lokale, serverlose Vektordatenbank (LanceDB) — keine externe Abhängigkeit für die Datenhaltung.
- **Offen/organisatorisch:** verschlüsseltes Backup von `.lancedb_data/` und der Pseudonym-Schlüssel,
  regelmäßiger **Restore-Test**.

---

## 4. Verschlüsselung (Art. 32 Abs. 1 lit. a)

- **Transport:** TLS-verschlüsselte Verbindung zu AWS Bedrock (Frankfurt).
- **At rest:** **FileVault (macOS) aktiviert** → vollständige Festplattenverschlüsselung des
  Arbeitsrechners inkl. `.lancedb_data/`, Audit-Log und Pseudonym-Schlüssel. *erledigt*
  - *Für die spätere ATOM: LUKS-Volume-Verschlüsselung einrichten.*

---

## 5. Verfahren zur regelmäßigen Überprüfung (Art. 32 Abs. 1 lit. d)

- Compliance-Nachweise des Auftragsverarbeiters AWS (als Anlage): **ISO 27001**, **BSI C5**,
  **SOC 2 Type II** (Bezug über AWS Artifact).
- **Offen/organisatorisch:** jährliche Überprüfung dieser TOM; Test der Pseudonymisierung
  nach Updates; Überprüfung der Zugriffsrechte.

---

## 6. Auftragskontrolle (Art. 28 DSGVO)

- AVV mit AWS über das **AWS GDPR DPA** (in AWS Artifact eingesehen/heruntergeladen).
- Region fest auf Frankfurt (`eu-central-1`).
- **Restrisiko CLOUD-Act** dokumentiert; Minderung durch lokale Pseudonymisierung; Zielbild:
  vollständige On-Prem-Verarbeitung auf GIGABYTE AI TOP ATOM (GB10 Blackwell) (kein Datenabfluss mehr).

---

## 7. Berufsrechtliche Besonderheit (§ 203 StGB / § 43e BRAO)

- Einschaltung von AWS als Dienstleister erfordert **Mandanten-Einwilligung** bzw. Berufung
  auf die berufsrechtliche Erlaubnis. **Organisatorisch durch die Kanzlei sicherzustellen.**

---

## 8. Löschkonzept (Art. 17 DSGVO)

- Löschung **pro Dokument** und **pro Akte** über Web-UI, API (`/api/delete`) und CLI (`delete`).
- **Aufbewahrungspflicht (vor Löschung beachten):** Handakten **6 Jahre** ab Ende des
  Kalenderjahres des Mandatsendes (**§ 50 BRAO**); Rechnungen/Kostennoten/Buchungsbelege
  **10 Jahre** (**§ 147 AO** / UStG). Erst **nach** Fristablauf sind Akte, Vektoren und
  Pseudonym-Schlüssel zu löschen; Löschung dann dokumentieren (Audit-Log).

---

## 9. Offene Punkte (Maßnahmenplan)

| # | Maßnahme | Verantwortlich | Frist | Status |
|---|---|---|---|---|
| 1 | Verschlüsselung at rest (FileVault) | IT | — | erledigt |
| 2 | Verschlüsseltes Backup + Restore-Test | IT | _[Datum]_ | offen |
| 3 | Löschfristen definieren & umsetzen | DSB | _[Datum]_ | offen |
| 4 | API-Key-Rotation festlegen | IT | _[Datum]_ | offen |
| 5 | Mandanten-Einwilligung § 203 StGB | Kanzlei/DSB | _[Datum]_ | offen |
| 6 | AWS DPA + Reports archivieren | DSB | _[Datum]_ | offen |
| 7 | Rollen-/Rechtekonzept (wer sieht welche Akte) | DSB | _[Datum]_ | offen |

---

_Erstellt: _[Datum]_ — Freigabe DSB: ____________________ — Unterschrift: _____________________
