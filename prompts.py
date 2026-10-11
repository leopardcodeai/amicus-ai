#!/usr/bin/env python3
"""
Promptverwaltung – standardisierte juristische Prompt-Vorlagen.

Vorlagen werden in einer JSON-Datei gehalten (lokal, versionierbar). Jede
Vorlage hat eine ID, Titel, Kategorie, einen optionalen System-Prompt und einen
Template-Text mit dem Platzhalter {{input}}, der durch die Nutzereingabe ersetzt
wird. Über die API/UI sind sie auswähl- und editierbar.
"""

from __future__ import annotations
import os
import json

STORE = os.environ.get("ANWALT_PROMPT_STORE", "./prompt_templates.json")

# Mitgelieferte juristische Standard-Vorlagen (Use-Case 2.4a).
DEFAULTS = [
    {
        "id": "mandatszusammenfassung",
        "title": "Mandatszusammenfassung",
        "category": "Analyse",
        "system": "Du bist erfahrene:r Rechtsanwält:in. Fasse präzise, strukturiert und nur auf Basis des Kontexts zusammen.",
        "template": "Erstelle eine strukturierte Mandatszusammenfassung (Beteiligte, Sachverhalt, rechtliche Fragen, Status, nächste Schritte) zu folgender Eingabe/Akte:\n\n{{input}}",
    },
    {
        "id": "vertragspruefung",
        "title": "Vertragsprüfung",
        "category": "Analyse",
        "system": "Du bist Vertragsjurist:in. Prüfe nüchtern, benenne Risiken und unklare Klauseln, schlage Formulierungen vor.",
        "template": "Prüfe den folgenden Vertrag/Kontext auf Risiken, problematische Klauseln, fehlende Regelungen und Verhandlungsspielräume. Gib konkrete Änderungsvorschläge:\n\n{{input}}",
    },
    {
        "id": "schriftsatzentwurf",
        "title": "Schriftsatzentwurf",
        "category": "Generierung",
        "system": "Du bist Rechtsanwält:in und verfasst Schriftsätze in sachlichem, juristisch präzisem Stil mit sauberer Gliederung (I., II., 1., a) ...).",
        "template": "Entwirf auf Basis des Kontexts einen Schriftsatz zu folgendem Anliegen. Arbeite die einschlägigen Argumente und Anspruchsgrundlagen heraus:\n\n{{input}}",
    },
    {
        "id": "rechtliche-stellungnahme",
        "title": "Rechtliche Stellungnahme",
        "category": "Generierung",
        "system": "Du arbeitest streng nach juristischer Methodik (Gutachtenstil), trennst Obersatz, Subsumtion, Ergebnis und kennzeichnest Unsicherheiten.",
        "template": "Erstelle eine rechtliche Stellungnahme zu folgender Frage. Benenne offene Punkte und Annahmen ausdrücklich:\n\n{{input}}",
    },
    {
        "id": "ki-text-vermenschlichen",
        "title": "KI-Text vermenschlichen",
        "category": "Stil",
        "system": "Du bist Lektor:in für juristische Texte.",
        "template": "Überarbeite den folgenden Text so, dass er natürlich, präzise und kanzleitauglich klingt, ohne den juristischen Inhalt zu verändern:\n\n{{input}}",
    },
    {
        "id": "halluzinations-check",
        "title": "Halluzinations-Check",
        "category": "Qualität",
        "system": "Du bist kritische:r Prüfer:in. Markiere jede Aussage, die NICHT durch den Kontext gedeckt ist.",
        "template": "Prüfe den folgenden Entwurf gegen den Kontext. Liste alle Aussagen auf, die nicht belegt sind oder erfunden sein könnten:\n\n{{input}}",
    },
]


def _load_raw() -> list[dict]:
    if os.path.exists(STORE):
        try:
            with open(STORE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def list_prompts() -> list[dict]:
    """Defaults gemerged mit benutzerdefinierten/überschriebenen Vorlagen."""
    custom = {p["id"]: p for p in _load_raw()}
    merged = {p["id"]: dict(p) for p in DEFAULTS}
    merged.update(custom)  # benutzerdefinierte gewinnen / ergänzen
    return list(merged.values())


def get_prompt(prompt_id: str) -> dict | None:
    for p in list_prompts():
        if p["id"] == prompt_id:
            return p
    return None


def save_prompt(prompt: dict) -> dict:
    """Erstellt/aktualisiert eine Vorlage (persistiert nur die Custom-Liste)."""
    if not prompt.get("id") or not prompt.get("template"):
        raise ValueError("id und template sind erforderlich")
    custom = {p["id"]: p for p in _load_raw()}
    custom[prompt["id"]] = {
        "id": prompt["id"],
        "title": prompt.get("title", prompt["id"]),
        "category": prompt.get("category", "Allgemein"),
        "system": prompt.get("system", ""),
        "template": prompt["template"],
    }
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(list(custom.values()), f, ensure_ascii=False, indent=2)
    return custom[prompt["id"]]


def delete_prompt(prompt_id: str) -> bool:
    custom = {p["id"]: p for p in _load_raw()}
    if prompt_id in custom:
        del custom[prompt_id]
        with open(STORE, "w", encoding="utf-8") as f:
            json.dump(list(custom.values()), f, ensure_ascii=False, indent=2)
        return True
    return False


def apply_template(prompt_id: str, user_input: str) -> tuple[str, str | None]:
    """Liefert (gefüllter_text, system_prompt) für eine Vorlage."""
    p = get_prompt(prompt_id)
    if not p:
        return user_input, None
    text = p["template"].replace("{{input}}", user_input or "")
    return text, (p.get("system") or None)
