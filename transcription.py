#!/usr/bin/env python3
"""
Lokale Diktat-Transkription (Use-Case 4).

Nutzt faster-whisper VOLLSTÄNDIG LOKAL (on-prem) – Diktate mit Mandantsbezug
verlassen die Maschine nicht. Auf der DGX Spark läuft das Modell auf der GPU
(`cuda`), lokal auf CPU.

Formate: wav/mp3/m4a/ogg/webm werden über das in faster-whisper gebündelte
ffmpeg/PyAV dekodiert. DSS (proprietär, Olympus) wird nicht nativ unterstützt
und muss vorgelagert konvertiert werden (separater Task).
"""

from __future__ import annotations
import os
import logging

logger = logging.getLogger("transcription")

MODEL_SIZE = os.environ.get("ANWALT_WHISPER_MODEL", "base")  # tiny|base|small|medium|large-v3
# Remote-Whisper-Container (OpenAI-kompatibel, z.B. faster-whisper-server).
# Gesetzt -> Transkription läuft im separaten GPU-Container statt in-process.
WHISPER_URL = os.environ.get("ANWALT_WHISPER_URL", "").rstrip("/")
_MODEL = None
_TRIED = False


def _transcribe_remote(path: str, language: str) -> dict:
    """Sendet die Audiodatei an den lokalen Whisper-Container (bleibt im VLAN)."""
    import httpx
    url = f"{WHISPER_URL}/v1/audio/transcriptions"
    with open(path, "rb") as f:
        files = {"file": (os.path.basename(path), f, "application/octet-stream")}
        data = {"model": MODEL_SIZE, "language": language, "response_format": "json"}
        r = httpx.post(url, files=files, data=data, timeout=600.0)
        r.raise_for_status()
    j = r.json()
    return {"text": (j.get("text") or "").strip(), "language": language, "segments": j.get("segments", [])}


def _device():
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def available() -> bool:
    return bool(WHISPER_URL) or _get_model() is not None


def _get_model():
    global _MODEL, _TRIED
    if _TRIED:
        return _MODEL
    _TRIED = True
    try:
        from faster_whisper import WhisperModel
        device, compute = _device()
        logger.info("Lade Whisper-Modell '%s' auf %s ...", MODEL_SIZE, device)
        _MODEL = WhisperModel(MODEL_SIZE, device=device, compute_type=compute)
    except Exception as e:
        logger.warning("Transkription nicht verfügbar (%s). Installiere faster-whisper.", e)
        _MODEL = None
    return _MODEL


def transcribe_file(path: str, language: str = "de") -> dict:
    """Transkribiert eine Audiodatei. Remote-Container (GPU) falls konfiguriert,
    sonst lokal in-process. Beides bleibt im Kanzlei-VLAN (kein Cloud-Versand)."""
    if WHISPER_URL:
        return _transcribe_remote(path, language)
    model = _get_model()
    if model is None:
        raise RuntimeError(
            "Transkription nicht verfügbar – faster-whisper ist nicht installiert/ladbar."
        )
    segments, info = model.transcribe(path, language=language, vad_filter=True)
    parts = []
    seg_list = []
    for seg in segments:
        parts.append(seg.text)
        seg_list.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                         "text": seg.text.strip()})
    return {
        "text": "".join(parts).strip(),
        "language": getattr(info, "language", language),
        "segments": seg_list,
    }
