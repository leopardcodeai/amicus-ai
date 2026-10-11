"""Verbrauchs-/Kosten-Tracking (usage.py) – ohne echte AWS-Aufrufe."""
import sys
import types
import pytest
import usage


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    f = tmp_path / "usage.json"
    monkeypatch.setattr(usage, "USAGE_LOG", str(f))
    return f


def test_record_und_kostenrechnung(ledger):
    usage.record("claude-haiku-4.5", 1000, 2000)   # 1000*1/1M + 2000*5/1M = 0.011
    usage.record("nova-lite", 10000, 0)            # 10000*0.06/1M = 0.0006
    s = usage.summary({"claude-haiku-4.5": "Haiku", "nova-lite": "Nova"})
    by = {r["key"]: r for r in s["month_models"]}
    assert by["claude-haiku-4.5"]["in_tokens"] == 1000
    assert by["claude-haiku-4.5"]["out_tokens"] == 2000
    assert by["claude-haiku-4.5"]["est_cost_usd"] == pytest.approx(0.011, abs=1e-6)
    assert by["nova-lite"]["est_cost_usd"] == pytest.approx(0.0006, abs=1e-6)
    assert s["month_cost_usd"] == pytest.approx(0.0116, abs=1e-6)
    assert s["currency"] == "USD"


def test_mehrere_requests_akkumulieren(ledger):
    usage.record("nova-lite", 100, 100)
    usage.record("nova-lite", 100, 100)
    s = usage.summary()
    row = next(r for r in s["month_models"] if r["key"] == "nova-lite")
    assert row["requests"] == 2
    assert row["in_tokens"] == 200


def test_nulltokens_werden_nicht_gebucht(ledger):
    usage.record("local", 0, 0)
    s = usage.summary()
    assert s["month_models"] == []
    assert s["month_cost_usd"] == 0


def test_preis_override_via_env(ledger, monkeypatch):
    monkeypatch.setenv("ANWALT_PRICES_JSON", '{"nova-lite": {"in": 10.0, "out": 10.0}}')
    usage.record("nova-lite", 1_000_000, 0)        # 1M in * $10/1M = $10
    s = usage.summary()
    row = next(r for r in s["month_models"] if r["key"] == "nova-lite")
    assert row["est_cost_usd"] == pytest.approx(10.0, abs=1e-6)


def test_budget_status_graceful_ohne_aws(monkeypatch):
    # Fake-boto3, dessen client() scheitert -> available=False mit Grund, kein Crash.
    fake = types.ModuleType("boto3")
    def _client(*a, **k):
        raise RuntimeError("AccessDenied: not authorized")
    fake.client = _client
    monkeypatch.setitem(sys.modules, "boto3", fake)
    st = usage.budget_status()
    assert st["available"] is False
    assert "reason" in st
