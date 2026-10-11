#!/usr/bin/env python3
"""
Multi-Provider LLM-Abstraktion.

Ein einheitliches Interface über mehrere Anbieter:
  * AWS Bedrock (Amazon Nova, Anthropic Claude – inkl. Opus 4.7 / Sonnet)
  * OpenAI (direkt via API-Key)
  * (vorbereitet) lokales LLM auf der ATOM (GB10) via OpenAI-kompatiblem Endpoint

Provider liefern reines Token-Streaming. Die Pseudonymisierung (DSGVO) wird
eine Ebene höher in rag_agent.generate_stream() angewandt, damit sie für ALLE
Provider gleichermaßen greift.
"""

from __future__ import annotations
import os

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")

# Modell-Registry: friendly key -> Metadaten. ALLE LLMs laufen über AWS Bedrock
# (eu-central-1, Frankfurt). Hinweis: OpenAI/GPT ist auf Bedrock NICHT verfügbar
# (Bedrock hostet Amazon Nova, Anthropic Claude, Llama, Mistral, Cohere u.a.).
# `cloud`: True = Daten verlassen die Maschine (relevant fürs Dual-Use-Routing).
MODELS: dict[str, dict] = {
    # --- Amazon Nova ---
    "nova-lite": {"provider": "bedrock", "model_id": "eu.amazon.nova-lite-v1:0",
                  "label": "Nova Lite (schnell)", "cloud": True},
    "nova-pro": {"provider": "bedrock", "model_id": "eu.amazon.nova-pro-v1:0",
                 "label": "Nova Pro", "cloud": True},
    # --- Anthropic Claude ---
    "claude-haiku-4.5": {"provider": "bedrock",
        "model_id": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        "label": "Claude Haiku 4.5", "cloud": True},
    "claude-sonnet": {"provider": "bedrock", "model_id": os.environ.get(
        "ANWALT_BEDROCK_SONNET", "eu.anthropic.claude-sonnet-4-6"),
        "label": "Claude Sonnet 4.6", "cloud": True},
    "claude-opus-4.6": {"provider": "bedrock",
        "model_id": "eu.anthropic.claude-opus-4-6-v1",
        "label": "Claude Opus 4.6 (Premium)", "cloud": True},
    "claude-opus-4.7": {"provider": "bedrock", "model_id": os.environ.get(
        "ANWALT_BEDROCK_OPUS", "eu.anthropic.claude-opus-4-7"),
        "label": "Claude Opus 4.7 (Premium)", "cloud": True},
    "claude-opus-4.8": {"provider": "bedrock",
        "model_id": "eu.anthropic.claude-opus-4-8",
        "label": "Claude Opus 4.8 (Premium)", "cloud": True},
    # --- Mistral AI ---
    "mistral-pixtral-large": {"provider": "bedrock",
        "model_id": "eu.mistral.pixtral-large-2502-v1:0",
        "label": "Mistral Pixtral Large (124B)", "cloud": True},
    # ATOM-Simulation: großes lokales Modell (123B) – nähert die Qualität an,
    # die der GB10/128GB on-prem mit einem ~70-123B-Modell liefern würde.
    "mistral-devstral-123b": {"provider": "bedrock",
        "model_id": "mistral.devstral-2-123b",
        "label": "Mistral Devstral 123B (ATOM-Simulation)", "cloud": True},
}

# Max. Output-Tokens. Für große PDFs/Gutachten hoch genug, damit Antworten nicht
# abgeschnitten werden. Code-Default 8192 (auch ohne ENV brauchbar, z.B. On-Prem);
# pro Modell via API begrenzt; bei Bedarf via ANWALT_MAX_TOKENS hochsetzen.
MAX_OUTPUT_TOKENS = int(os.environ.get("ANWALT_MAX_TOKENS", "8192"))

# Anzeigename des Providers/Backends für die UI ("woher kam die Antwort?")
PROVIDER_LABELS = {"bedrock": "AWS Bedrock", "openai": "OpenAI", "local": "Lokal (ATOM/GB10)"}


def model_meta(model_key: str) -> dict:
    """Anzeige-Metadaten für ein Modell (Label, Provider-Backend, lokal/Cloud)."""
    m = MODELS.get(model_key) or MODELS[DEFAULT_MODEL_KEY]
    return {
        "key": model_key if model_key in MODELS else DEFAULT_MODEL_KEY,
        "label": m["label"],
        "provider": m["provider"],
        "backend": PROVIDER_LABELS.get(m["provider"], m["provider"]),
        "cloud": m["cloud"],
    }

# Lokales ATOM-LLM (OpenAI-kompatibler Endpoint), nur aktiv wenn konfiguriert.
# Zielmodell on-prem: Gemma 4 (31B) auf dem GB10. Tag an die ollama-/vLLM-
# Bezeichnung anpassen, sobald verfügbar (z.B. "gemma4:31b").
_LOCAL_BASE_URL = os.environ.get("ANWALT_LOCAL_LLM_URL")  # z.B. http://ollama:11434/v1
_LOCAL_MODEL = os.environ.get("ANWALT_LOCAL_LLM_MODEL", "gemma4:31b")
if _LOCAL_BASE_URL:
    # Label spiegelt den TATSÄCHLICH konfigurierten Tag (kein fix verdrahtetes
    # "Gemma 4 31B", das vom realen ollama-Tag abweichen könnte).
    MODELS["local"] = {"provider": "local", "model_id": _LOCAL_MODEL,
                       "label": f"{_LOCAL_MODEL} (lokal/ATOM)", "cloud": False}

# Default-Modell: lokal bevorzugen, wenn ein On-Prem-Endpoint konfiguriert ist
# (reiner ATOM-Betrieb ohne AWS), sonst Cloud-Haiku. Per ENV überschreibbar.
DEFAULT_MODEL_KEY = os.environ.get("ANWALT_DEFAULT_MODEL") or (
    "local" if _LOCAL_BASE_URL else "claude-haiku-4.5")
if DEFAULT_MODEL_KEY not in MODELS:
    DEFAULT_MODEL_KEY = "claude-haiku-4.5"


_ACCESS_CACHE: dict[str, bool] = {}


def _local_reachable() -> bool:
    """Schneller Erreichbarkeitscheck des lokalen LLM-Endpoints (ollama/vLLM),
    damit das lokale Modell im UI nur erscheint, wenn es wirklich antwortet."""
    if not _LOCAL_BASE_URL:
        return False
    import urllib.request
    import urllib.error
    try:
        urllib.request.urlopen(_LOCAL_BASE_URL, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # Server antwortet (z.B. 404) -> erreichbar
    except Exception:
        return False


def _probe_access(model_key: str) -> bool:
    """Prüft (einmal, gecacht) per Mini-Aufruf, ob das Modell zugänglich ist.

    So erscheinen im UI nur Modelle, die wirklich funktionieren (z.B. Claude Opus
    erst, nachdem der Bedrock-Modellzugang freigeschaltet wurde)."""
    if model_key in _ACCESS_CACHE:
        return _ACCESS_CACHE[model_key]
    ok = True
    meta = MODELS.get(model_key, {})
    if meta.get("provider") == "bedrock":
        try:
            provider, model_id = get_provider(model_key)
            provider.client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "ok"}]}],
                inferenceConfig={"maxTokens": 1, "temperature": 0},
            )
        except Exception:
            ok = False
    elif meta.get("provider") == "openai":
        ok = bool(os.environ.get("OPENAI_API_KEY"))
    elif meta.get("provider") == "local":
        ok = _local_reachable()
    _ACCESS_CACHE[model_key] = ok
    return ok


def list_models(check_access: bool = True) -> list[dict]:
    """Modelle für die UI. Standardmäßig nur tatsächlich zugängliche Modelle."""
    out = []
    for k, v in MODELS.items():
        if check_access and not _probe_access(k):
            continue
        out.append({"key": k, "label": v["label"], "provider": v["provider"], "cloud": v["cloud"]})
    return out


# --- Provider-Implementierungen ----------------------------------------------
class _BedrockProvider:
    def __init__(self):
        import boto3
        self.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    def stream(self, model_id, messages, system, max_tokens=None, temperature=0.1, usage_sink=None):
        max_tokens = max_tokens or MAX_OUTPUT_TOKENS
        response = self.client.converse_stream(
            modelId=model_id,
            messages=[{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages],
            system=[{"text": system}] if system else [],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        for event in response.get("stream", []):
            if "contentBlockDelta" in event:
                delta = event["contentBlockDelta"]["delta"]
                if "text" in delta:
                    yield delta["text"]
            elif "metadata" in event and usage_sink is not None:
                u = event["metadata"].get("usage", {}) or {}
                usage_sink["input_tokens"] = u.get("inputTokens", 0)
                usage_sink["output_tokens"] = u.get("outputTokens", 0)


class _OpenAICompatProvider:
    """OpenAI und OpenAI-kompatible lokale Endpoints (vLLM, Ollama)."""
    def __init__(self, base_url=None, api_key_env="OPENAI_API_KEY"):
        from openai import OpenAI
        api_key = os.environ.get(api_key_env) or ("local" if base_url else None)
        if not api_key:
            raise RuntimeError(
                f"{api_key_env} ist nicht gesetzt – OpenAI-Provider nicht nutzbar."
            )
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def stream(self, model_id, messages, system, max_tokens=None, temperature=0.1, usage_sink=None):
        max_tokens = max_tokens or MAX_OUTPUT_TOKENS
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        stream = self.client.chat.completions.create(
            model=model_id, messages=msgs, max_tokens=max_tokens,
            temperature=temperature, stream=True,
            stream_options={"include_usage": True},   # finaler Chunk trägt usage
        )
        for chunk in stream:
            if usage_sink is not None and getattr(chunk, "usage", None):
                usage_sink["input_tokens"] = getattr(chunk.usage, "prompt_tokens", 0)
                usage_sink["output_tokens"] = getattr(chunk.usage, "completion_tokens", 0)
            if not chunk.choices:        # usage-only Abschluss-Chunk
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content


_CACHE: dict[str, object] = {}


def get_provider(model_key: str):
    """Liefert (provider_instance, model_id) für einen Modell-Key."""
    meta = MODELS.get(model_key) or MODELS[DEFAULT_MODEL_KEY]
    provider = meta["provider"]
    if provider not in _CACHE:
        if provider == "bedrock":
            _CACHE[provider] = _BedrockProvider()
        elif provider == "openai":
            _CACHE[provider] = _OpenAICompatProvider()
        elif provider == "local":
            _CACHE[provider] = _OpenAICompatProvider(base_url=_LOCAL_BASE_URL)
        else:
            raise RuntimeError(f"Unbekannter Provider: {provider}")
    return _CACHE[provider], meta["model_id"]
