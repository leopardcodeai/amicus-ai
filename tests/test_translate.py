"""Übersetzung: Zielsprachen-Mapping & Fallback (ohne echten LLM-Aufruf)."""
from rag_agent import LLMService


def _capture_system_prompt(target_lang):
    """Ruft translate() auf, fängt aber den an stream() übergebenen System-Prompt ab."""
    svc = LLMService(pseudonymize=False)
    captured = {}

    def fake_stream(text, ctx, key, system_prompt=None, extra_user=None):
        captured["system"] = system_prompt
        return iter(())

    svc.stream = fake_stream
    list(svc.translate("Hallo Welt", target_lang, "claude-haiku-4.5"))
    return captured["system"]


def test_lang_names_enthaelt_grundsprachen():
    for code in ("de", "en", "fr", "nl"):
        assert code in LLMService.LANG_NAMES


def test_translate_setzt_zielsprache_englisch():
    assert "Englisch" in _capture_system_prompt("en")


def test_translate_setzt_zielsprache_franzoesisch():
    assert "Französisch" in _capture_system_prompt("fr")


def test_unbekannter_code_faellt_auf_englisch():
    # 'xx' ist unbekannt -> sinnvoller Default statt unsinniger Zielsprache.
    sys = _capture_system_prompt("xx")
    assert "Englisch" in sys
    assert "xx" not in sys
