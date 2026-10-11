"""Provider-Registry & Routing (ohne echte AWS-Aufrufe)."""
import providers
from providers import (
    MODELS, DEFAULT_MODEL_KEY, model_meta, list_models, get_provider, PROVIDER_LABELS,
)


def test_default_modell_ist_registriert():
    assert DEFAULT_MODEL_KEY in MODELS


def test_model_meta_felder_fuer_cloud_modell():
    m = model_meta("claude-haiku-4.5")
    assert m["key"] == "claude-haiku-4.5"
    assert m["provider"] == "bedrock"
    assert m["backend"] == "AWS Bedrock"
    assert m["cloud"] is True
    assert "label" in m


def test_model_meta_unbekannt_faellt_auf_default():
    m = model_meta("gibt-es-nicht")
    assert m["key"] == DEFAULT_MODEL_KEY


def test_list_models_ohne_access_check_liefert_alle():
    models = list_models(check_access=False)
    keys = {m["key"] for m in models}
    assert DEFAULT_MODEL_KEY in keys
    for m in models:
        assert {"key", "label", "provider", "cloud"}.issubset(m.keys())


def test_get_provider_liefert_korrekte_model_id():
    _provider, model_id = get_provider("claude-haiku-4.5")
    assert model_id == MODELS["claude-haiku-4.5"]["model_id"]


def test_lokales_backend_label_ist_atom():
    # Anzeigekonsistenz: lokales Backend heißt überall ATOM/GB10 (nicht DGX).
    assert PROVIDER_LABELS["local"] == "Lokal (ATOM/GB10)"


def test_local_modell_registrierung_haengt_an_env(monkeypatch):
    # 'local' ist nur registriert, wenn ANWALT_LOCAL_LLM_URL gesetzt ist.
    if "local" not in MODELS:
        assert providers._LOCAL_BASE_URL in (None, "")
    else:
        assert MODELS["local"]["provider"] == "local"


def test_default_ist_haiku_ohne_lokalen_endpoint():
    # Ohne ANWALT_LOCAL_LLM_URL (Testumgebung) bleibt der Cloud-Default erhalten.
    if not providers._LOCAL_BASE_URL:
        assert DEFAULT_MODEL_KEY == "claude-haiku-4.5"


def test_local_reachable_false_ohne_url(monkeypatch):
    # Kein lokaler Endpoint -> nicht erreichbar (Gemma erscheint nicht im UI).
    monkeypatch.setattr(providers, "_LOCAL_BASE_URL", None)
    assert providers._local_reachable() is False


def test_probe_access_local_nutzt_reachability(monkeypatch):
    # 'local'-Provider-Zugang hängt an der Endpoint-Erreichbarkeit, nicht an AWS.
    monkeypatch.setitem(providers.MODELS, "local",
                        {"provider": "local", "model_id": "x", "label": "x", "cloud": False})
    providers._ACCESS_CACHE.pop("local", None)
    monkeypatch.setattr(providers, "_local_reachable", lambda: True)
    assert providers._probe_access("local") is True
    providers._ACCESS_CACHE.pop("local", None)
    monkeypatch.setattr(providers, "_local_reachable", lambda: False)
    assert providers._probe_access("local") is False
