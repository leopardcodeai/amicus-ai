#!/usr/bin/env python3
"""Verbrauchs- & Kosten-Tracking für LLM-Aufrufe.

Zwei Quellen (siehe System-Status):
  1) LOKAL: tatsächliche Tokens DIESER App pro Modell (aus den usage-Metadaten der
     Bedrock-/OpenAI-Antwort) -> Echtzeit, kostenlos, kein kontoweiter Datenabfluss.
     Kosten = Tokens x editierbare Preis-Tabelle (Schätzung).
  2) AWS-BUDGET: das echte, in AWS Budgets gesetzte Limit + Ist/Forecast für
     "verbleibend" (nur wenn IAM-Recht budgets:ViewBudget vorhanden).

Preise/Budgets sind über ENV überschreibbar; die Defaults sind Schätzungen.
"""
from __future__ import annotations
import os
import json
import threading
import datetime

# Ledger-Datei (persistent), Pfad ueber ANWALT_USAGE_LOG umstellbar.
USAGE_LOG = os.environ.get("ANWALT_USAGE_LOG", "./.usage.json")
_LOCK = threading.Lock()

# Offizielle AWS-Bedrock-Listenpreise (On-Demand, USD pro 1 Mio. Tokens), Stand 06/2026.
# Anthropic-/Nova-/Mistral-Modelle sind bei Bedrock regionsübergreifend einheitlich
# bepreist (kein EU/Frankfurt-Aufschlag). Sonnet/Opus-Basistarif (Kontext <=200K).
# Per ANWALT_PRICES_JSON überschreibbar, falls AWS die Preise ändert.
_DEFAULT_PRICES = {
    "nova-lite":             {"in": 0.06,  "out": 0.24},
    "nova-pro":              {"in": 0.80,  "out": 3.20},
    "claude-haiku-4.5":      {"in": 1.00,  "out": 5.00},
    "claude-sonnet":         {"in": 3.00,  "out": 15.00},
    "claude-opus-4.6":       {"in": 5.00,  "out": 25.00},
    "claude-opus-4.7":       {"in": 5.00,  "out": 25.00},
    "claude-opus-4.8":       {"in": 5.00,  "out": 25.00},
    "mistral-pixtral-large": {"in": 2.00,  "out": 6.00},
    "mistral-devstral-123b": {"in": 2.00,  "out": 6.00},
    "local":                 {"in": 0.0,   "out": 0.0},   # On-Prem: keine Token-Kosten
}


def _prices() -> dict:
    override = os.environ.get("ANWALT_PRICES_JSON")
    if override:
        try:
            merged = dict(_DEFAULT_PRICES)
            merged.update(json.loads(override))
            return merged
        except Exception:
            pass
    return _DEFAULT_PRICES


def _month() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def _load() -> dict:
    try:
        with open(USAGE_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        tmp = USAGE_LOG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, USAGE_LOG)
    except Exception:
        pass


def record(model_key: str, input_tokens: int, output_tokens: int) -> None:
    """Bucht einen Aufruf (nur wenn echte Tokens vorliegen) thread-sicher ins Ledger."""
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    if input_tokens <= 0 and output_tokens <= 0:
        return
    with _LOCK:
        data = _load()
        month = data.setdefault(_month(), {})
        m = month.setdefault(model_key, {"requests": 0, "in_tokens": 0, "out_tokens": 0})
        m["requests"] += 1
        m["in_tokens"] += input_tokens
        m["out_tokens"] += output_tokens
        _save(data)


def _cost(model_key: str, in_tok: int, out_tok: int, prices: dict) -> float:
    p = prices.get(model_key, {"in": 0.0, "out": 0.0})
    return (in_tok / 1_000_000) * p["in"] + (out_tok / 1_000_000) * p["out"]


def summary(model_labels: dict | None = None) -> dict:
    """Aggregiert den lokalen Verbrauch (aktueller Monat + gesamt) inkl. Kostenschätzung."""
    data = _load()
    prices = _prices()
    labels = model_labels or {}
    cur = data.get(_month(), {})

    def rows(bucket: dict) -> list[dict]:
        out = []
        for key, m in sorted(bucket.items()):
            out.append({
                "key": key,
                "label": labels.get(key, key),
                "requests": m.get("requests", 0),
                "in_tokens": m.get("in_tokens", 0),
                "out_tokens": m.get("out_tokens", 0),
                "est_cost_usd": round(_cost(key, m.get("in_tokens", 0), m.get("out_tokens", 0), prices), 4),
            })
        return out

    # Gesamt über alle Monate
    total_bucket: dict = {}
    for _mon, models in data.items():
        for key, m in models.items():
            t = total_bucket.setdefault(key, {"requests": 0, "in_tokens": 0, "out_tokens": 0})
            t["requests"] += m.get("requests", 0)
            t["in_tokens"] += m.get("in_tokens", 0)
            t["out_tokens"] += m.get("out_tokens", 0)

    month_rows = rows(cur)
    total_rows = rows(total_bucket)
    return {
        "month": _month(),
        "currency": "USD",
        "prices_note": "Offizielle AWS-Bedrock-Listenpreise (On-Demand, USD/1M Tokens, Stand 06/2026); via ANWALT_PRICES_JSON anpassbar.",
        "month_models": month_rows,
        "month_cost_usd": round(sum(r["est_cost_usd"] for r in month_rows), 4),
        "total_models": total_rows,
        "total_cost_usd": round(sum(r["est_cost_usd"] for r in total_rows), 4),
    }


def budget_status() -> dict:
    """Echtes AWS-Budget (Limit/Ist/Forecast/verbleibend) aus AWS Budgets.

    Budgets ist ein globaler Dienst (us-east-1). Liefert available=False mit Grund,
    wenn kein Budget gesetzt ist oder das IAM-Recht budgets:ViewBudget fehlt."""
    name = os.environ.get("ANWALT_AWS_BUDGET_NAME")  # optional: bestimmtes Budget
    try:
        import boto3
        sts = boto3.client("sts")
        account_id = sts.get_caller_identity()["Account"]
        client = boto3.client("budgets", region_name="us-east-1")
        resp = client.describe_budgets(AccountId=account_id, MaxResults=100)
        budgets = resp.get("Budgets", [])
        if not budgets:
            return {"available": False, "reason": "Kein AWS-Budget gesetzt."}
        b = None
        if name:
            b = next((x for x in budgets if x.get("BudgetName") == name), None)
        b = b or budgets[0]
        limit = b.get("BudgetLimit", {})
        spend = b.get("CalculatedSpend", {})
        actual = spend.get("ActualSpend", {})
        forecast = spend.get("ForecastedSpend", {})
        limit_amt = float(limit.get("Amount", 0) or 0)
        actual_amt = float(actual.get("Amount", 0) or 0)
        return {
            "available": True,
            "name": b.get("BudgetName"),
            "currency": limit.get("Unit", "USD"),
            "limit": limit_amt,
            "actual": actual_amt,
            "forecast": float(forecast.get("Amount", 0) or 0) if forecast else None,
            "remaining": round(limit_amt - actual_amt, 2),
            "time_unit": b.get("TimeUnit", "MONTHLY"),
        }
    except Exception as e:
        msg = type(e).__name__
        if "AccessDenied" in str(e) or "not authorized" in str(e):
            msg = "Kein IAM-Recht (budgets:ViewBudget)."
        return {"available": False, "reason": msg}
