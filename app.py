#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import uuid
import json
import shutil
import asyncio
import secrets
from pathlib import Path
from fastapi import (
    FastAPI, UploadFile, File, Form, HTTPException, Depends, Security, Request,
)
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from typing import Optional

# Import existing RAG components from rag_agent
from rag_agent import (
    Embedder, VectorDatabase, LLMService, DocumentProcessor, MODELS,
    DEFAULT_MATTER, PSEUDONYMIZE, AWS_REGION, DEFAULT_EMBED_MODEL,
    DEFAULT_MODEL_KEY, RAW_DIR, audit, system_info,
)
from typing import List
from pseudonymizer import ner_available, Pseudonymizer
from providers import list_models, model_meta, get_provider
import prompts as prompt_store
import transcription
try:
    import usage  # optionales Verbrauchs-/Kosten-Tracking
except Exception:
    usage = None
import research
import retrieval
import time

# --- Konfiguration (per Umgebungsvariablen) -----------------------------------
import logging

def _read_app_version() -> str:
    """App-Version aus der VERSION-Datei. MAJOR.MINOR pflegt man von Hand;
    den PATCH stempelt die CI pro Deploy (Run-Nummer). Lokal: '.dev'-Suffix."""
    try:
        v = (Path(__file__).parent / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.dev"
    return v if v.count(".") >= 2 else f"{v}.dev"

APP_VERSION = _read_app_version()

# API-Key: ist keiner gesetzt, wird einer generiert und beim Start ausgegeben.
API_KEY = os.environ.get("ANWALT_API_KEY") or secrets.token_urlsafe(24)
# Admin-Key für Log-Auslesen (Wartung). BEWUSST KEIN Fallback auf API_KEY:
# das Kunden-Passwort darf niemals Admin-Endpunkte (Logs!) öffnen. Ist kein
# Admin-Key gesetzt, sind Admin-Endpunkte deaktiviert (fail-closed).
ADMIN_KEY = os.environ.get("ANWALT_ADMIN_KEY") or None
# Umgebung: "prod" (echte Kanzleidaten) oder "test" (synthetische Dummy-Daten)
ANWALT_ENV = os.environ.get("ANWALT_ENV", "test")
APP_LOG = os.environ.get("ANWALT_APP_LOG", "./app.log")
# Auth: in Produktion übernimmt Cloudflare Access (E-Mail+OTP auf der Subdomain)
# den Türsteher. ANWALT_DISABLE_AUTH=1 für lokale Tests ohne Key.
AUTH_DISABLED = os.environ.get("ANWALT_DISABLE_AUTH", "0") == "1"
# Header, den Cloudflare Access nach erfolgreichem Login setzt:
CF_ACCESS_HEADER = "cf-access-authenticated-user-email"

# Strukturiertes Datei-Logging (über Tailscale/Admin auslesbar)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(APP_LOG), logging.StreamHandler()],
)
log = logging.getLogger("anwalt.gateway")
log.info("Privacy Gateway startet in ENV=%s", ANWALT_ENV)
# Erlaubte Browser-Origins (KEIN Wildcard im Produktivbetrieb).
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ANWALT_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",") if o.strip()
]
# Upload-Limits
MAX_UPLOAD_BYTES = int(os.environ.get("ANWALT_MAX_UPLOAD_MB", "25")) * 1024 * 1024
ALLOWED_EXTS = {".pdf", ".docx", ".txt", ".md"}
# Keine '.' oder '/' -> verhindert Path-Traversal, da matter_id Teil der RAW_DIR-Pfade ist.
_MATTER_RE = re.compile(r"^[A-Za-z0-9_\- ]{1,64}$")

app = FastAPI(title="Anwalt Agent RAG API", description="Lokales RAG + AWS Bedrock (DSGVO)")

# CORS: nur definierte Origins, mit Credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- Authentifizierung --------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(request: Request, api_key: str = Security(_api_key_header)):
    # 1) Lokaler Testmodus  2) Cloudflare-Access-Login  3) Fallback API-Key
    if AUTH_DISABLED:
        return "local"
    cf_email = request.headers.get(CF_ACCESS_HEADER)
    if cf_email:
        return cf_email
    if api_key and secrets.compare_digest(api_key, API_KEY):
        return api_key
    raise HTTPException(status_code=401, detail="Zugang nur über Cloudflare-Access (Login) oder API-Key.")


def require_admin(request: Request, api_key: str = Security(_api_key_header)):
    if AUTH_DISABLED:
        return "local"
    if ADMIN_KEY and api_key and secrets.compare_digest(api_key, ADMIN_KEY):
        return api_key
    raise HTTPException(status_code=401, detail="Admin-Key erforderlich.")


def _tail(path: str, n: int) -> list[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return [ln.rstrip("\n") for ln in f.readlines()[-n:]]
    except Exception:
        return []


def _safe_matter(matter_id: Optional[str]) -> str:
    matter_id = (matter_id or DEFAULT_MATTER).strip()
    if not _MATTER_RE.match(matter_id):
        raise HTTPException(status_code=400, detail="Ungültige matter_id (Akten-ID).")
    return matter_id


# --- RAG-Instanzen (einmalig laden) -------------------------------------------
embedder = Embedder()
db = VectorDatabase(embedder)
llm = LLMService()
ALLOWED_AUDIO = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".aac"}


class ChatRequest(BaseModel):
    query: str
    model: str = DEFAULT_MODEL_KEY
    matter_id: Optional[str] = None
    prompt_id: Optional[str] = None       # optionale Prompt-Vorlage
    web_research: bool = False            # DSGVO-sichere Web-Recherche zuschalten
    scopes: Optional[List[str]] = None    # geteilte Kategorien: allgemein/rechtsprechung
    mentions: Optional[List[str]] = None  # parent_ids exakt referenzierter Normen (#)


class TranslateRequest(BaseModel):
    text: str
    target_lang: str = "en"               # "en" oder "de"
    model: str = "claude-sonnet"


class PromptModel(BaseModel):
    id: str
    title: Optional[str] = None
    category: Optional[str] = None
    system: Optional[str] = None
    template: str


_ENCRYPTION_CACHE: Optional[dict] = None


def _encryption_context() -> dict:
    """Verschlüsselung at rest umgebungsabhängig UND tatsächlich geprüft, statt
    blind 'True' zu behaupten: macOS via fdesetup, Linux via crypt-Geräte. Ergebnis
    wird gecacht (ändert sich zur Laufzeit nicht). encryption_at_rest=None = unbekannt."""
    global _ENCRYPTION_CACHE
    if _ENCRYPTION_CACHE is not None:
        return _ENCRYPTION_CACHE
    import platform
    import subprocess

    def probe() -> dict:
        sysname = platform.system()
        try:
            if sysname == "Darwin":
                out = subprocess.run(["fdesetup", "status"], capture_output=True,
                                     text=True, timeout=4).stdout or ""
                on = "FileVault is On" in out
                return {"encryption_at_rest": on,
                        "encryption_label": "FileVault aktiv (macOS)" if on
                        else "FileVault NICHT aktiv (macOS)"}
            if sysname == "Linux":
                out = subprocess.run(["lsblk", "-o", "TYPE"], capture_output=True,
                                     text=True, timeout=4).stdout or ""
                on = "crypt" in out
                return {"encryption_at_rest": on,
                        "encryption_label": "LUKS/dm-crypt aktiv (On-Prem)" if on
                        else "Laufwerksverschlüsselung nicht erkannt"}
        except Exception:
            pass
        return {"encryption_at_rest": None, "encryption_label": "nicht verifiziert"}

    _ENCRYPTION_CACHE = probe()
    return _ENCRYPTION_CACHE


@app.get("/api/version")
async def version():
    """App-Version (bewusst ohne Auth: enthält nichts Sensibles und erlaubt den
    Ist-online-schon-die-neue-Version?-Check direkt vom Login-Screen aus)."""
    return {"version": APP_VERSION, "env": ANWALT_ENV}


@app.get("/api/status", dependencies=[Depends(require_api_key)])
async def status():
    """Sicherheits-/Datenschutz-Status für die UI-Indikatoren."""
    return {
        "app_version": APP_VERSION,
        "local_processing": True,
        "pseudonymize": PSEUDONYMIZE,
        "ner_active": ner_available() if PSEUDONYMIZE else False,
        "matter_separation": True,
        "audit_log": True,
        "transport_tls": True,
        "region": AWS_REGION,
        "embed_model": DEFAULT_EMBED_MODEL,
        "transcription": transcription.available(),
        "providers": sorted(set(m["provider"] for m in list_models(check_access=False))),
        "retrieval_mode": retrieval.RETRIEVAL_MODE,
        "reranker": retrieval.RERANK_MODEL if retrieval.reranker_available() else None,
        "env": ANWALT_ENV,
        **_encryption_context(),
    }


@app.get("/api/region-status", dependencies=[Depends(require_api_key)])
async def region_status():
    """Live-Erreichbarkeit/Latenz der AWS-Region Frankfurt (Health-Proxy).

    Echte Rechenzentrums-Auslastung gibt AWS nicht heraus; wir messen die
    Round-Trip-Latenz eines Mini-Aufrufs als ehrlichen Status-/Last-Indikator."""
    # Probe-Modell konfigurierbar (nicht hart "nova-lite"); günstiges Bedrock-Modell.
    probe_model = os.environ.get("ANWALT_REGION_PROBE_MODEL", "nova-lite")

    def _aws_configured() -> bool:
        try:
            import boto3
            return boto3.Session().get_credentials() is not None
        except Exception:
            return False

    def _ping():
        # Reiner On-Prem-Betrieb ohne AWS: kein Ausfall, sondern "nur lokal".
        if not _aws_configured():
            return {"online": False, "mode": "local-only"}
        try:
            provider, model_id = get_provider(probe_model)
            t0 = time.perf_counter()
            provider.client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "ok"}]}],
                inferenceConfig={"maxTokens": 1, "temperature": 0},
            )
            ms = int((time.perf_counter() - t0) * 1000)
            load = "niedrig" if ms < 800 else "mittel" if ms < 2000 else "hoch"
            return {"online": True, "mode": "cloud", "latency_ms": ms, "load": load}
        except Exception as e:
            return {"online": False, "mode": "error", "error": str(e)[:120]}

    def _upcoming_eu_models():
        """Watcher: melden, sobald neue Claude-Modelle ein EU-Inference-Profil
        bekommen (DSGVO: nur eu.* garantiert EU-Routing; global.* darf weltweit)."""
        watch = {"Sonnet 5": "eu.anthropic.claude-sonnet-5",
                 "Fable 5": "eu.anthropic.claude-fable-5"}
        try:
            import boto3
            b = boto3.client("bedrock", region_name=AWS_REGION)
            profiles = {p["inferenceProfileId"]
                        for p in b.list_inference_profiles()["inferenceProfileSummaries"]}
            return [{"label": name, "eu_profile": any(pid.startswith(prefix) for pid in profiles)}
                    for name, prefix in watch.items()]
        except Exception:
            return []  # kein IAM-Recht/offline -> Anzeige einfach weglassen

    res = await asyncio.to_thread(_ping)
    res["region"] = AWS_REGION
    res["upcoming_eu_models"] = await asyncio.to_thread(_upcoming_eu_models)
    return res


@app.get("/api/usage", dependencies=[Depends(require_api_key)])
async def usage_status():
    """Verbrauch & Budget: lokal getrackte Tokens/Kosten pro Modell (Echtzeit,
    Schätzung) + echtes AWS-Budget (verbleibend), falls in AWS Budgets gesetzt."""
    if usage is None:
        return {"usage": None, "budget": {"available": False, "reason": "Usage-Modul nicht verfügbar."}}
    labels = {k: v["label"] for k, v in MODELS.items()}
    summary = await asyncio.to_thread(usage.summary, labels)
    budget = await asyncio.to_thread(usage.budget_status)
    return {"usage": summary, "budget": budget}


@app.get("/api/models", dependencies=[Depends(require_api_key)])
async def get_models():
    """Liste der LLMs für die UI. Fällt auf die volle Liste zurück, falls der
    Zugriffs-Check keine liefert (z.B. AWS-Creds noch nicht gesetzt) -> Auswahl
    bleibt immer möglich; ein Aufruf schlägt dann ggf. mit klarer Meldung fehl."""
    models = await asyncio.to_thread(list_models, True)
    if not models:
        models = list_models(check_access=False)
    return {"models": models, "default": DEFAULT_MODEL_KEY}


@app.get("/api/logs", dependencies=[Depends(require_admin)])
async def get_logs(lines: int = 200):
    """Logs der (Test-)Umgebung – auslesbar als Admin über Tailscale/im VLAN.

    Liefert App-Log und Audit-Log. Im Test-Profil sind das ausschließlich
    Vorgänge mit synthetischen Daten."""
    lines = max(1, min(lines, 2000))
    from rag_agent import AUDIT_LOG
    return {
        "env": ANWALT_ENV,
        "app_log": _tail(APP_LOG, lines),
        "audit_log": _tail(AUDIT_LOG, lines),
    }


@app.get("/api/knowledge", dependencies=[Depends(require_api_key)])
async def knowledge():
    """Knowledge-Base-Übersicht: Dateien je Kategorie + Speicherbelegung/Hardware."""
    ov = await asyncio.to_thread(db.knowledge_overview)
    sysinfo = await asyncio.to_thread(system_info)
    return {"overview": ov, "system": sysinfo}


@app.get("/api/sections", dependencies=[Depends(require_api_key)])
async def sections(q: str = "", matter_id: Optional[str] = None):
    """Normen-Vorschläge für #-Mentions (exakte Gesetzestexte referenzieren)."""
    mid = _safe_matter(matter_id) if matter_id else None
    res = await asyncio.to_thread(
        db.get_sections, q, mid, ["allgemein", "rechtsprechung"], 12
    )
    return {"sections": res}


@app.get("/api/prompts", dependencies=[Depends(require_api_key)])
async def get_prompts():
    """Alle Prompt-Vorlagen (Defaults + benutzerdefinierte)."""
    return {"prompts": prompt_store.list_prompts()}


@app.post("/api/prompts", dependencies=[Depends(require_api_key)])
async def upsert_prompt(p: PromptModel):
    try:
        saved = await asyncio.to_thread(prompt_store.save_prompt, p.model_dump())
        return {"status": "success", "prompt": saved}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/prompts/delete", dependencies=[Depends(require_api_key)])
async def remove_prompt(payload: dict):
    pid = payload.get("id")
    if not pid:
        raise HTTPException(status_code=400, detail="id erforderlich")
    ok = await asyncio.to_thread(prompt_store.delete_prompt, pid)
    return {"status": "success" if ok else "not_found"}


@app.get("/api/documents", dependencies=[Depends(require_api_key)])
async def get_documents(matter_id: Optional[str] = None):
    """Returns active documents, optionally restricted to one matter."""
    try:
        mid = _safe_matter(matter_id) if matter_id else None
        stats = await asyncio.to_thread(db.get_stats, mid)
        return {
            "total_chunks": stats["total_chunks"],
            "documents": stats["sources"],
            "matters": stats["matters"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/document", dependencies=[Depends(require_api_key)])
async def get_document(source: str, matter_id: Optional[str] = None):
    """Klartext eines Dokuments für die Schnellansicht (Popup)."""
    try:
        mid = _safe_matter(matter_id) if matter_id else None
        text = await asyncio.to_thread(db.get_document_text, source, mid)
        if not text:
            raise HTTPException(status_code=404, detail="Dokument nicht gefunden.")
        return {"source": source, "matter_id": mid, "text": text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ingest", dependencies=[Depends(require_api_key)])
async def ingest_files(
    files: list[UploadFile] = File(...),
    matter_id: str = Form(DEFAULT_MATTER),
    law_book: str = Form(""),
    scope: str = Form("mandat"),
):
    """Upload -> Roh-Ablage (Original) + Verarbeitung (Doku: parsen | MP3: lokal
    transkribieren) -> chunken, vektorisieren, speichern. Immer Roh UND verarbeitet."""
    matter_id = _safe_matter(matter_id)
    temp_dir = "./.temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    raw_dir = os.path.join(RAW_DIR, matter_id)        # matter_id ist validiert
    os.makedirs(raw_dir, exist_ok=True)

    # Vorab prüfen, BEVOR irgendetwas in die Roh-Ablage geschrieben wird:
    # (a) alle Endungen gültig, (b) wenn Audio dabei, muss Transkription verfügbar sein.
    # Verhindert inkonsistente Teil-Ingests (Roh-Datei ohne DB-Eintrag).
    has_audio = False
    for file in files:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext in ALLOWED_AUDIO:
            has_audio = True
        elif ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail=f"Format {ext or '?'} nicht unterstützt.")
    if has_audio and not transcription.available():
        raise HTTPException(status_code=503, detail="Transkription nicht verfügbar (Whisper).")

    parsed_docs, transcripts = [], []
    try:
        for file in files:
            ext = os.path.splitext(file.filename or "")[1].lower()
            is_audio = ext in ALLOWED_AUDIO
            if ext not in ALLOWED_EXTS and not is_audio:
                raise HTTPException(status_code=400,
                    detail=f"Format {ext or '?'} nicht unterstützt.")
            original_name = os.path.basename(file.filename or f"upload{ext}")
            temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}{ext}")
            limit = MAX_UPLOAD_BYTES * 4 if is_audio else MAX_UPLOAD_BYTES
            size = 0
            with open(temp_path, "wb") as buffer:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        buffer.close()
                        os.remove(temp_path)
                        raise HTTPException(status_code=413, detail="Datei zu groß.")
                    buffer.write(chunk)

            # ROH-Ablage: Original immer beim Mandanten sichern
            shutil.copyfile(temp_path, os.path.join(raw_dir, original_name))

            if is_audio:
                if not transcription.available():
                    raise HTTPException(status_code=503,
                        detail="Transkription nicht verfügbar (Whisper).")
                result = await asyncio.to_thread(transcription.transcribe_file, temp_path, "de")
                text = result.get("text", "")
                with open(os.path.join(raw_dir, original_name + ".txt"), "w", encoding="utf-8") as f:
                    f.write(text)            # Transkript als Roh-Textdatei
                if text.strip():
                    parsed_docs.append({"text": text, "page": 1, "source": original_name})
                transcripts.append({"source": original_name, "text": text})
            else:
                docs = await asyncio.to_thread(DocumentProcessor.parse_file, temp_path)
                for d in docs:
                    d["source"] = original_name
                parsed_docs.extend(docs)
            os.remove(temp_path)

        if parsed_docs:
            await asyncio.to_thread(db.insert_documents, parsed_docs, matter_id, law_book, scope)
        stats = await asyncio.to_thread(db.get_stats, matter_id)
        audit("ingest_upload", matter_id=matter_id, files=len(files),
              audio=len(transcripts), scope=scope)
        return {
            "status": "success",
            "message": f"{len(files)} Datei(en) in Akte '{matter_id}' (roh + verarbeitet) abgelegt.",
            "total_chunks": stats["total_chunks"],
            "documents": stats["sources"],
            "transcripts": transcripts,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/chat", dependencies=[Depends(require_api_key)])
async def chat(request: ChatRequest):
    """Streamt die Antwort des gewählten Providers; PII wird lokal pseudonymisiert.
    Optional: Prompt-Vorlage anwenden und/oder DSGVO-sichere Web-Recherche."""
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    model_key = request.model if request.model in MODELS else DEFAULT_MODEL_KEY
    matter_id = _safe_matter(request.matter_id) if request.matter_id else None

    # Optionale Prompt-Vorlage anwenden (füllt {{input}}, liefert ggf. System-Prompt)
    system_prompt = None
    effective_query = query
    if request.prompt_id:
        effective_query, system_prompt = await asyncio.to_thread(
            prompt_store.apply_template, request.prompt_id, query
        )

    scopes = request.scopes if request.scopes is not None else ["allgemein", "rechtsprechung"]

    # Rechenschaftspflicht (Art. 5 Abs. 2): zentralen LLM-Vorgang protokollieren –
    # nur Metadaten, KEIN Klartext der Anfrage.
    audit("chat", matter_id=matter_id, model=model_key,
          cloud=model_meta(model_key)["cloud"],
          web_research=bool(request.web_research), scopes=scopes)

    async def event_generator():
        def status(s):
            return f"[STATUS]: {s}\n\n"

        # 1) Lokale Suche (Embedding + Hybrid + Reranker) – auf CPU der langsamste Schritt
        yield status("Durchsuche Wissensbasis (Embedding + Reranking, lokal)…")
        results = await asyncio.to_thread(db.search, query, 5, matter_id, None, scopes)

        if request.mentions:
            yield status("Referenzierte Normen werden geladen…")
            mentioned = await asyncio.to_thread(db.get_parents, request.mentions)
            existing = {r.get("parent_id") for r in results}
            results[:0] = [m for m in mentioned if m.get("parent_id") not in existing]

        web_results, web_context = [], None
        if request.web_research:
            # Drittdienst-Suche nur, wenn die Namens-Erkennung verfügbar ist – sonst
            # könnten Klarnamen an die Suchmaschine gelangen (DSGVO, siehe Fail-closed).
            if PSEUDONYMIZE and not ner_available():
                yield status("Web-Recherche übersprungen: Namens-Erkennung (NER) nicht verfügbar.")
            else:
                yield status("DSGVO-sichere Web-Recherche (nur pseudonymisierte Anfrage)…")
                safe_q = await asyncio.to_thread(lambda: Pseudonymizer().pseudonymize(query))
                web_results = await asyncio.to_thread(research.web_search, safe_q, 5)
                web_context = research.format_for_context(web_results)
                # query_sent NICHT als Klartext loggen – nur Länge/Trefferzahl.
                audit("web_research", matter_id=matter_id,
                      query_chars=len(safe_q), hits=len(web_results))

        citations = [
            {"index": i + 1, "source": r["source"], "page": r["page"],
             "section": r.get("section", ""), "absatz": r.get("absatz", ""),
             "law_book": r.get("law_book", "")}
            for i, r in enumerate(results)
        ]
        web_meta = [{"title": w.get("title", ""), "url": w.get("url", "")} for w in web_results]
        meta = {"type": "metadata", "citations": citations, "web": web_meta,
                "model": model_meta(model_key)}
        yield f"[METADATA]: {json.dumps(meta)}\n\n"

        # 2) Pseudonymisieren + Anfrage ans Modell (Cloud Bedrock / lokal)
        backend = model_meta(model_key)["backend"]
        yield status(f"Pseudonymisierung & Anfrage an {backend}…")
        gen = llm.stream(effective_query, results, model_key,
                         system_prompt=system_prompt, extra_user=web_context)
        loop = asyncio.get_event_loop()
        sentinel = object()
        first = True
        while True:
            chunk = await loop.run_in_executor(None, lambda: next(gen, sentinel))
            if chunk is sentinel:
                break
            if first and chunk:
                yield status("Antwort wird erstellt…")  # erstes Token da
                first = False
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/translate", dependencies=[Depends(require_api_key)])
async def translate(request: TranslateRequest):
    """Übersetzt Text DE<->EN über Bedrock; PII wird lokal pseudonymisiert."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kein Text angegeben.")
    model_key = request.model if request.model in MODELS else "claude-sonnet"
    audit("translate", target=request.target_lang, chars=len(text))

    async def event_generator():
        gen = llm.translate(text, request.target_lang, model_key)
        loop = asyncio.get_event_loop()
        sentinel = object()
        while True:
            chunk = await loop.run_in_executor(None, lambda: next(gen, sentinel))
            if chunk is sentinel:
                break
            yield chunk

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/transcribe", dependencies=[Depends(require_api_key)])
async def transcribe(file: UploadFile = File(...), language: str = Form("de")):
    """Transkribiert ein Diktat LOKAL (on-prem, kein Cloud-Versand)."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_AUDIO:
        raise HTTPException(
            status_code=400,
            detail=f"Audioformat {ext or '?'} nicht unterstützt. Erlaubt: {', '.join(sorted(ALLOWED_AUDIO))}. "
                   f"(DSS bitte vorab konvertieren.)",
        )
    if not transcription.available():
        raise HTTPException(
            status_code=503,
            detail="Transkription nicht verfügbar (faster-whisper nicht installiert/ladbar).",
        )
    temp_dir = "./.temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}{ext}")
    try:
        size = 0
        with open(temp_path, "wb") as buffer:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES * 4:  # Audio darf größer sein
                    buffer.close()
                    os.remove(temp_path)
                    raise HTTPException(status_code=413, detail="Audiodatei zu groß.")
                buffer.write(chunk)
        result = await asyncio.to_thread(transcription.transcribe_file, temp_path, language)
        audit("transcribe", chars=len(result["text"]))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@app.post("/api/delete", dependencies=[Depends(require_api_key)])
async def delete_data(payload: dict):
    """DSGVO Art. 17: löscht ein Dokument oder eine komplette Akte."""
    source = payload.get("source")
    matter_id = payload.get("matter_id")
    if matter_id:
        matter_id = _safe_matter(matter_id)
    try:
        if source:
            await asyncio.to_thread(db.delete_document, source, matter_id)
            return {"status": "success", "message": f"Dokument '{source}' gelöscht."}
        elif matter_id:
            await asyncio.to_thread(db.delete_matter, matter_id)
            return {"status": "success", "message": f"Akte '{matter_id}' gelöscht."}
        raise HTTPException(status_code=400, detail="source oder matter_id erforderlich.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/clear", dependencies=[Depends(require_api_key)])
async def clear_database():
    """Setzt die gesamte Vektordatenbank zurück."""
    try:
        await asyncio.to_thread(db.clear_database)
        return {"status": "success", "message": "Database cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Statisches Frontend
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("\n" + "=" * 60)
    print(f"  ANWALT AGENT — API-Key für diese Sitzung:\n  {API_KEY}")
    print("  (per ANWALT_API_KEY fest setzen, um ihn beizubehalten)")
    print("=" * 60 + "\n")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
