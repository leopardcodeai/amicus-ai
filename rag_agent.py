#!/usr/bin/env python3
from __future__ import annotations
import os
import re
import json
import uuid
import shutil
import argparse
import datetime
import docx
import lancedb
import pyarrow as pa
import torch
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt
from rich.live import Live

from pseudonymizer import Pseudonymizer, ner_available
try:
    import usage  # optionales Verbrauchs-/Kosten-Tracking
except Exception:  # darf den Betrieb nie blockieren
    usage = None

# Initialize Rich Console
console = Console()

# Configuration (per Umgebungsvariablen überschreibbar)
DB_DIR = os.environ.get("ANWALT_DB_DIR", "./.lancedb_data")
TABLE_NAME = "documents"
# bge-m3: starkes mehrsprachiges Embedding (Deutsch + Recht), 1024-dim, 8k Kontext.
# Per ANWALT_EMBED_MODEL überschreibbar (z.B. multilingual-e5-base, schneller/kleiner).
DEFAULT_EMBED_MODEL = os.environ.get("ANWALT_EMBED_MODEL", "BAAI/bge-m3")
EMBED_DIM = int(os.environ.get("ANWALT_EMBED_DIM", "1024"))  # bge-m3 = 1024
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
AUDIT_LOG = os.environ.get("ANWALT_AUDIT_LOG", "./audit_log.jsonl")
# Roh-Ablage: hochgeladene Originaldateien (Dokumente + MP3) + Transkripte je Akte.
RAW_DIR = os.environ.get("ANWALT_RAW_DIR", "./.raw_store")
# Pseudonymisierung vor Cloud-Versand standardmäßig AN (DSGVO).
PSEUDONYMIZE = os.environ.get("ANWALT_PSEUDONYMIZE", "1") != "0"
DEFAULT_MATTER = "default"


def system_info() -> dict:
    """Echte Speicher-/Hardware-Werte des *laufenden Hosts* (für die KB-Übersicht).
    Portabel: liefert auf einem Server dessen Werte, lokal die des Mac."""
    import shutil
    import subprocess
    import platform
    def _dir_size(path):
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    info = {}

    # RAM – plattformübergreifend via sysconf (Linux & macOS), sonst Fallbacks
    ram = 0
    try:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        ram = 0
    if not ram and platform.system() == "Darwin":
        try:
            ram = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
        except Exception:
            ram = 0
    info["ram_bytes"] = ram

    # CPU-Bezeichnung je nach Betriebssystem
    cpu = ""
    sysname = platform.system()
    try:
        if sysname == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        cpu = line.split(":", 1)[1].strip()
                        break
        elif sysname == "Darwin":
            cpu = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip()
    except Exception:
        cpu = ""
    info["cpu"] = cpu or platform.processor() or "Server-CPU"

    # Host-Label
    if sysname == "Darwin":
        info["host"] = "Mac (lokal)"
    else:
        info["host"] = f"{sysname} (Server)"

    try:
        du = shutil.disk_usage("/")
        info["disk_total"], info["disk_free"] = du.total, du.free
    except Exception:
        info["disk_total"] = info["disk_free"] = 0
    info["kb_bytes"] = _dir_size(DB_DIR) if os.path.isdir(DB_DIR) else 0
    return info


def _sql_escape(value: str) -> str:
    """Escapes single quotes to prevent injection in LanceDB filter strings."""
    return str(value).replace("'", "''")


def audit(action: str, **fields):
    """Append-only Audit-Log (DSGVO Art. 5 Abs. 2 Rechenschaftspflicht)."""
    try:
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "action": action,
            **fields,
        }
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # Logging darf den Betrieb nie blockieren
        console.print(f"[yellow]Audit-Log Fehler: {e}[/yellow]")


def _delete_raw_files(matter_id: str, source: str) -> int:
    """Entfernt die Roh-Originaldatei eines Dokuments (und ggf. dessen Transkript)
    aus RAW_DIR/<matter_id>/. Path-Traversal wird durch basename() unterbunden."""
    removed = 0
    base = os.path.basename(source or "")
    if not base:
        return 0
    raw_dir = os.path.join(RAW_DIR, matter_id)
    for candidate in (base, base + ".txt"):
        path = os.path.join(raw_dir, candidate)
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            console.print(f"[yellow]Roh-Datei konnte nicht gelöscht werden ({path}): {e}[/yellow]")
    return removed


def _delete_raw_dir(matter_id: str) -> bool:
    """Entfernt den kompletten Roh-Ablage-Ordner einer Akte (DSGVO Art. 17)."""
    raw_dir = os.path.join(RAW_DIR, os.path.basename(matter_id or ""))
    if matter_id and os.path.isdir(raw_dir):
        try:
            shutil.rmtree(raw_dir)
            return True
        except OSError as e:
            console.print(f"[yellow]Roh-Ordner konnte nicht gelöscht werden ({raw_dir}): {e}[/yellow]")
    return False


# Modelle/Provider zentral aus providers.py (Multi-Provider: Bedrock + OpenAI + lokal)
from providers import MODELS, DEFAULT_MODEL_KEY, get_provider  # noqa: E402 (bewusst nach Config)


class DocumentProcessor:
    @staticmethod
    def clean_text(text: str) -> str:
        """Cleans whitespace and redundant newlines from text."""
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

    # Erkennt Rechtsnorm-Überschriften: "Artikel 59", "§ 5", "Art. 3a", "Abschnitt II"
    _HEADING_RE = re.compile(
        r"(?m)^\s*((?:Artikel|Art\.?|§)\s*\d+[a-z]?(?:\s*[a-z])?)\s*$"
    )

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
        """Generisches Sliding-Window-Chunking (Fallback ohne Rechtsstruktur)."""
        chunks = []
        if not text:
            return chunks
        words = text.split(" ")
        current_chunk, current_length = [], 0
        for word in words:
            current_chunk.append(word)
            current_length += len(word) + 1
            if current_length >= chunk_size:
                chunks.append(" ".join(current_chunk))
                overlap_words, overlap_len = [], 0
                for w in reversed(current_chunk):
                    overlap_words.insert(0, w)
                    overlap_len += len(w) + 1
                    if overlap_len >= overlap:
                        break
                current_chunk, current_length = overlap_words, overlap_len
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        return chunks

    # Absatz-Marker am Zeilenanfang: "(1)", "(12)"
    _ABSATZ_RE = re.compile(r"(?m)^\s*\((\d{1,2})\)\s")

    @classmethod
    def chunk_document(cls, text: str, law_book: str = "", parent_max: int = 2000,
                       child_max: int = 600, overlap: int = 120) -> list[dict]:
        """Parent-Child-Chunking für Rechtstexte (Blueprint-konform).

        - PARENT = vollständiger Artikel/§ inkl. Überschrift (Kontext fürs LLM).
        - CHILD  = einzelner Absatz (oder Teil davon) – das Such-Ziel, mit Norm-
          Präfix versehen. Trägt Metadaten: section, absatz, law_book, parent_id.

        So trifft die Suche feingranular (Absatz-Ebene), das LLM bekommt aber den
        ganzen Paragrafen und kann absatz-genau zitieren (z.B. "Artikel 59 Abs. 2").

        Rückgabe: Liste von {text, parent_text, section, absatz, law_book, parent_id}.
        """
        if not text:
            return []

        def children_from(body: str, section: str, parent_text: str, pid: str) -> list[dict]:
            out = []
            ab = list(cls._ABSATZ_RE.finditer(body))
            if ab:  # nach Absätzen aufteilen
                blocks = []
                if ab[0].start() > 0:
                    head = body[: ab[0].start()].strip()
                    if head:
                        blocks.append(("", head))
                for j, a in enumerate(ab):
                    s = a.start()
                    e = ab[j + 1].start() if j + 1 < len(ab) else len(body)
                    blocks.append((f"Abs. {a.group(1)}", body[s:e].strip()))
            else:
                blocks = [("", body)]
            for absatz, blk in blocks:
                if not blk.strip():
                    continue
                pieces = cls.chunk_text(blk, child_max, overlap) if len(blk) > child_max else [blk]
                for p in pieces:
                    tag = f"{law_book + ' ' if law_book else ''}{section}{' ' + absatz if absatz else ''}".strip()
                    out.append({
                        "text": f"[{tag}]\n{p}",
                        "parent_text": parent_text,
                        "section": section,
                        "absatz": absatz,
                        "law_book": law_book,
                        "parent_id": pid,
                    })
            return out

        matches = list(cls._HEADING_RE.finditer(text))
        results: list[dict] = []

        if len(matches) < 3:
            # Keine Rechtsstruktur -> generisch, jeder Chunk ist sein eigener Parent
            for c in cls.chunk_text(text, parent_max, overlap):
                pid = uuid.uuid4().hex
                results.append({"text": c, "parent_text": c, "section": "",
                                "absatz": "", "law_book": law_book, "parent_id": pid})
            return results

        if matches[0].start() > 0:
            pre = text[: matches[0].start()].strip()
            for c in cls.chunk_text(pre, parent_max, overlap):
                if c.strip():
                    pid = uuid.uuid4().hex
                    results.append({"text": c, "parent_text": c, "section": "",
                                    "absatz": "", "law_book": law_book, "parent_id": pid})

        for i, m in enumerate(matches):
            label = re.sub(r"\s+", " ", m.group(1)).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if not body:
                continue
            first_line = body.split("\n", 1)[0].strip()
            title = first_line if 0 < len(first_line) <= 80 else ""
            section = f"{label} – {title}" if title else label
            parent_text = f"{section}\n{body}"
            pid = uuid.uuid4().hex
            results.extend(children_from(body, section, parent_text, pid))
        return results

    @staticmethod
    def _marker_to_markdown(filepath: str) -> str | None:
        """Konvertiert ein PDF lokal mit Marker zu Markdown (falls installiert)."""
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered
            conv = PdfConverter(artifact_dict=create_model_dict())
            return text_from_rendered(conv(filepath))[0]
        except Exception as e:
            console.print(f"[yellow]Marker nicht verfügbar ({e}) – nutze pypdf.[/yellow]")
            return None

    @classmethod
    def parse_file(cls, filepath: str) -> list[dict]:
        """Parses a file and returns a list of dictionaries with text and metadata."""
        ext = os.path.splitext(filepath)[1].lower()
        results = []

        if ext == ".pdf":
            # Optional: Marker (lokal!) für strukturerhaltendes Markdown (Tabellen,
            # Fußnoten, verschachtelte Aufzählungen). Aktivierung: ANWALT_PDF_PARSER=marker
            # und `pip install marker-pdf`. Fällt sonst sauber auf pypdf zurück.
            md = cls._marker_to_markdown(filepath) if os.environ.get("ANWALT_PDF_PARSER") == "marker" else None
            if md:
                results.append({"text": cls.clean_text(md), "page": 1,
                                "source": os.path.basename(filepath)})
            else:
                reader = PdfReader(filepath)
                for i, page in enumerate(reader.pages):
                    text = page.extract_text()
                    if text and text.strip():
                        results.append({
                            "text": cls.clean_text(text),
                            "page": i + 1,
                            "source": os.path.basename(filepath)
                        })
        elif ext == ".docx":
            doc = docx.Document(filepath)
            full_text = "\n\n".join([para.text for para in doc.paragraphs if para.text.strip()])
            if full_text.strip():
                results.append({
                    "text": cls.clean_text(full_text),
                    "page": 1,
                    "source": os.path.basename(filepath)
                })
        elif ext in [".txt", ".md"]:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            if text.strip():
                results.append({
                    "text": cls.clean_text(text),
                    "page": 1,
                    "source": os.path.basename(filepath)
                })
        else:
            console.print(f"[yellow]Skipping unsupported file format: {ext}[/yellow]")
        
        return results


class Embedder:
    def __init__(self):
        """Loads the embedding model locally. Uses CUDA (ATOM/GB10), Apple MPS, or CPU.

        Die Auswahl ist zukunftssicher: auf der GIGABYTE AI TOP ATOM (GB10) wird
        automatisch `cuda` genutzt, ohne Codeänderung.
        """
        if torch.cuda.is_available():
            device = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
        with console.status(f"[bold blue]Loading local Embedding Model on {device}...[/bold blue]"):
            self.model = SentenceTransformer(DEFAULT_EMBED_MODEL, device=device)
        _dim = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dimension = _dim() or EMBED_DIM
        # e5-Modelle erwarten Prefixe ("query:" / "passage:") für beste Qualität.
        self._is_e5 = "e5" in DEFAULT_EMBED_MODEL.lower()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generates embeddings for a list of document chunks (passages)."""
        if self._is_e5:
            texts = [f"passage: {t}" for t in texts]
        embeddings = self.model.encode(
            texts, show_progress_bar=False, normalize_embeddings=True, batch_size=32
        )
        return [emb.tolist() for emb in embeddings]

    def embed_query(self, query: str) -> list[float]:
        """Generates embedding for a single query."""
        if self._is_e5:
            query = f"query: {query}"
        return self.model.encode(
            query, show_progress_bar=False, normalize_embeddings=True
        ).tolist()


class VectorDatabase:
    def __init__(self, embedder: Embedder):
        self.embedder = embedder
        self.db = lancedb.connect(DB_DIR)
        self.schema = pa.schema([
            ("vector", pa.list_(pa.float32(), list_size=self.embedder.dimension)),
            ("text", pa.string()),
            ("source", pa.string()),
            ("page", pa.int32()),
            ("chunk_id", pa.string()),
            ("matter_id", pa.string()),  # Mandanten-/Aktentrennung (DSGVO)
            ("section", pa.string()),    # Norm-/Abschnittsüberschrift (z.B. "Artikel 59 – Datenschutz")
            ("absatz", pa.string()),     # Absatz innerhalb der Norm (z.B. "Abs. 2")
            ("law_book", pa.string()),   # Gesetzbuch/Verordnung (z.B. "VO 1107/2009", "BDSG")
            ("parent_id", pa.string()),  # Parent-Child: Verweis auf den vollen Paragrafen
            ("parent_text", pa.string()),# Vollständiger Paragraf (Kontext fürs LLM)
            ("scope", pa.string()),      # Kategorie: allgemein | rechtsprechung | mandat
        ])

    def list_tables(self) -> list[str]:
        res = self.db.list_tables()
        if hasattr(res, "tables"):
            return res.tables
        return res

    def get_table(self):
        """Opens or creates the LanceDB table."""
        if TABLE_NAME in self.list_tables():
            return self.db.open_table(TABLE_NAME)
        else:
            return self.db.create_table(TABLE_NAME, schema=self.schema)

    def insert_documents(self, parsed_docs: list[dict], matter_id: str = DEFAULT_MATTER,
                         law_book: str = "", scope: str = "mandat"):
        """Chunks parsed documents (Parent-Child), embeds children, stores them.

        `scope`: allgemein (Gesetze/Muster) | rechtsprechung (Urteile) | mandat (Akte).
        `matter_id` trennt Mandantsakten; `law_book` (z.B. "VO 1107/2009") fürs Zitat.
        """
        table = self.get_table()

        data_to_insert = []
        texts_to_embed = []
        metadata_list = []

        for doc in parsed_docs:
            chunks = DocumentProcessor.chunk_document(doc["text"], law_book=law_book)
            for chunk in chunks:
                texts_to_embed.append(chunk["text"])
                metadata_list.append({
                    "source": doc["source"],
                    "page": doc["page"],
                    "chunk_id": str(uuid.uuid4()),
                    "matter_id": matter_id,
                    "section": chunk.get("section", ""),
                    "absatz": chunk.get("absatz", ""),
                    "law_book": chunk.get("law_book", "") or law_book,
                    "parent_id": chunk.get("parent_id", ""),
                    "parent_text": chunk.get("parent_text", chunk["text"]),
                    "scope": scope,
                })

        if not texts_to_embed:
            console.print("[yellow]No text chunks found to ingest.[/yellow]")
            return

        with console.status(f"[bold green]Generating local embeddings for {len(texts_to_embed)} chunks...[/bold green]"):
            vectors = self.embedder.embed_texts(texts_to_embed)

        for vector, text, meta in zip(vectors, texts_to_embed, metadata_list):
            data_to_insert.append({
                "vector": vector,
                "text": text,
                "source": meta["source"],
                "page": meta["page"],
                "chunk_id": meta["chunk_id"],
                "matter_id": meta["matter_id"],
                "section": meta["section"],
                "absatz": meta["absatz"],
                "law_book": meta["law_book"],
                "parent_id": meta["parent_id"],
                "parent_text": meta["parent_text"],
                "scope": meta["scope"],
            })

        table.add(data_to_insert)
        self._fts_ready = False  # Volltext-Index nach neuem Inhalt neu aufbauen
        sources = sorted(set(m["source"] for m in metadata_list))
        audit("ingest", matter_id=matter_id, sources=sources, chunks=len(data_to_insert))
        console.print(f"[bold green]Successfully ingested {len(data_to_insert)} chunks from {len(sources)} file(s) into matter '{matter_id}'.[/bold green]")

    @staticmethod
    def _build_where(matter_id, scopes):
        """Where-Klausel: aktuelle Mandantsakte ODER geteilte Kategorien (allgemein/rechtsprechung)."""
        clauses = []
        if matter_id:
            clauses.append(f"(scope = 'mandat' AND matter_id = '{_sql_escape(matter_id)}')")
        for sc in (scopes or []):
            if sc in ("allgemein", "rechtsprechung"):
                clauses.append(f"scope = '{_sql_escape(sc)}'")
        # Fail-closed: ohne erlaubten Mandanten/Scope NIEMALS alle Akten zeigen
        # (Mandantentrennung). '1 = 0' -> 0 Treffer statt ungefilterter Suche.
        return " OR ".join(clauses) if clauses else "1 = 0"

    def _vector_hits(self, table, query, where, n):
        qv = self.embedder.embed_query(query)
        s = table.search(qv)
        if where:
            s = s.where(where)
        return s.limit(n).to_list()

    def _ensure_fts(self, table):
        """Erstellt (einmal) den Volltext-Index auf der text-Spalte."""
        if getattr(self, "_fts_ready", False):
            return True
        try:
            table.create_fts_index("text", replace=True)
            self._fts_ready = True
        except Exception as e:
            console.print(f"[yellow]FTS-Index nicht verfügbar: {e}[/yellow]")
            self._fts_ready = False
        return self._fts_ready

    def _fts_hits(self, table, query, where, n):
        try:
            s = table.search(query, query_type="fts")
            if where:
                s = s.where(where)
            return s.limit(n).to_list()
        except Exception:
            return []

    def search(self, query: str, limit: int = 5, matter_id: str | None = None,
               mode: str | None = None, scopes: list | None = None) -> list[dict]:
        """Hybrid-Suche (Vektor + Volltext + Cross-Encoder-Reranking), scope-gefiltert.

        `scopes`: geteilte Kategorien, die zusätzlich zur Mandantsakte durchsucht
        werden (z.B. ["allgemein","rechtsprechung"]). `mode`: "hybrid" | "vector".
        """
        import retrieval
        if TABLE_NAME not in self.list_tables():
            return []
        table = self.db.open_table(TABLE_NAME)
        mode = mode or retrieval.RETRIEVAL_MODE
        where = self._build_where(matter_id, scopes)

        if mode == "vector":
            raw = self._vector_hits(table, query, where, max(limit * 3, 10))
            results, seen = [], set()
            for r in raw:
                pid = r.get("parent_id") or r.get("chunk_id")
                if pid in seen:
                    continue
                seen.add(pid)
                if r.get("parent_text"):
                    r["matched_text"] = r.get("text")
                    r["text"] = r["parent_text"]
                results.append(r)
                if len(results) >= limit:
                    break
            audit("search", matter_id=matter_id, hits=len(results), mode="vector")
            return results

        # --- Hybrid ---
        cand_n = max(25, limit * 5)
        vec = self._vector_hits(table, query, where, cand_n)
        self._ensure_fts(table)
        fts = self._fts_hits(table, query, where, cand_n)

        # Kandidaten nach chunk_id zusammenführen
        by_id = {c["chunk_id"]: c for c in vec}
        for c in fts:
            by_id.setdefault(c["chunk_id"], c)

        if fts:
            ranks = retrieval.rrf_merge([
                [c["chunk_id"] for c in vec],
                [c["chunk_id"] for c in fts],
            ])
            ordered = sorted(by_id.values(), key=lambda c: ranks.get(c["chunk_id"], 0), reverse=True)
        else:
            ordered = vec  # nur Vektor, falls FTS nicht verfügbar

        # Cross-Encoder-Reranking auf der Kandidatenmenge (Child-Ebene)
        reranked = retrieval.rerank(query, ordered[:cand_n], cand_n)

        # Parent-Child-Rekonstruktion: pro Paragraf nur den bestplatzierten Child
        # behalten und den VOLLEN Paragrafen (parent_text) als Treffer-Text liefern.
        results, seen = [], set()
        for r in reranked:
            pid = r.get("parent_id") or r.get("chunk_id")
            if pid in seen:
                continue
            seen.add(pid)
            if r.get("parent_text"):
                r["matched_text"] = r.get("text")   # der konkrete Such-Treffer (Absatz)
                r["text"] = r["parent_text"]         # voller Paragraf als LLM-Kontext
            results.append(r)
            if len(results) >= limit:
                break
        audit("search", matter_id=matter_id, hits=len(results),
              mode="hybrid", reranked=retrieval.reranker_available())
        return results

    def delete_document(self, source: str, matter_id: str | None = None) -> int:
        """Löscht alle Chunks eines Dokuments (DSGVO Art. 17 Recht auf Löschung)."""
        if TABLE_NAME not in self.list_tables():
            return 0
        table = self.db.open_table(TABLE_NAME)
        cond = f"source = '{_sql_escape(source)}'"
        if matter_id:
            cond += f" AND matter_id = '{_sql_escape(matter_id)}'"
        table.delete(cond)
        # DSGVO Art. 17: auch die Roh-Ablage (Original + ggf. Transkript) entfernen,
        # sonst bleiben die sensibelsten Klartextdaten dauerhaft liegen.
        raw_removed = _delete_raw_files(matter_id, source) if matter_id else 0
        audit("delete_document", source=source, matter_id=matter_id, raw_removed=raw_removed)
        console.print(f"[bold red]Deleted document '{source}' (raw: {raw_removed}).[/bold red]")
        return 1

    def delete_matter(self, matter_id: str) -> int:
        """Löscht eine komplette Akte/Mandat (DSGVO Art. 17)."""
        if TABLE_NAME not in self.list_tables():
            return 0
        table = self.db.open_table(TABLE_NAME)
        table.delete(f"matter_id = '{_sql_escape(matter_id)}'")
        # DSGVO Art. 17: kompletten Roh-Ablage-Ordner der Akte entfernen.
        raw_removed = _delete_raw_dir(matter_id)
        audit("delete_matter", matter_id=matter_id, raw_dir_removed=raw_removed)
        console.print(f"[bold red]Deleted matter '{matter_id}' (raw dir: {raw_removed}).[/bold red]")
        return 1

    def clear_database(self):
        """Drops the table to clear database."""
        if TABLE_NAME in self.list_tables():
            self.db.drop_table(TABLE_NAME)
            console.print("[bold red]Database cleared successfully.[/bold red]")
        else:
            console.print("[yellow]Database is already empty.[/yellow]")

    def knowledge_overview(self) -> dict:
        """Übersicht aller geladenen Dateien gruppiert nach Kategorie (scope)."""
        empty = {"allgemein": [], "rechtsprechung": [], "mandate": {}, "total_chunks": 0}
        if TABLE_NAME not in self.list_tables():
            return empty
        at = self.db.open_table(TABLE_NAME).to_arrow()
        names = at.schema.names
        n = len(at)
        scope_col = at["scope"].to_pylist() if "scope" in names else ["mandat"] * n
        src_col = at["source"].to_pylist()
        matter_col = at["matter_id"].to_pylist() if "matter_id" in names else [DEFAULT_MATTER] * n
        law_col = at["law_book"].to_pylist() if "law_book" in names else [""] * n

        agg = {}  # (scope, matter, source) -> {chunks, law_book}
        for sc, src, mt, lb in zip(scope_col, src_col, matter_col, law_col):
            key = (sc or "mandat", mt, src)
            d = agg.setdefault(key, {"chunks": 0, "law_book": lb})
            d["chunks"] += 1
        out = {"allgemein": [], "rechtsprechung": [], "mandate": {}, "total_chunks": n}
        for (sc, mt, src), d in sorted(agg.items()):
            entry = {"source": src, "chunks": d["chunks"], "law_book": d["law_book"]}
            if sc == "allgemein":
                out["allgemein"].append(entry)
            elif sc == "rechtsprechung":
                out["rechtsprechung"].append(entry)
            else:
                out["mandate"].setdefault(mt, []).append(entry)
        return out

    def get_sections(self, query: str = "", matter_id: str | None = None,
                     scopes: list | None = None, limit: int = 12) -> list[dict]:
        """Distinkte Normen (für #-Mention-Autocomplete)."""
        if TABLE_NAME not in self.list_tables():
            return []
        at = self.db.open_table(TABLE_NAME).to_arrow()
        names = at.schema.names
        if "section" not in names:
            return []
        n = len(at)
        rows = zip(
            at["section"].to_pylist(),
            at["law_book"].to_pylist() if "law_book" in names else [""] * n,
            at["parent_id"].to_pylist() if "parent_id" in names else [""] * n,
            at["scope"].to_pylist() if "scope" in names else ["mandat"] * n,
            at["matter_id"].to_pylist() if "matter_id" in names else [DEFAULT_MATTER] * n,
        )
        q = (query or "").lower()
        seen, out = set(), []
        for section, law, pid, sc, mt in rows:
            if not section:
                continue
            # Scope-Sichtbarkeit wie bei der Suche
            visible = (sc in (scopes or []) and sc in ("allgemein", "rechtsprechung")) or \
                      (sc == "mandat" and matter_id and mt == matter_id)
            if scopes is None and not matter_id:
                visible = True
            if not visible:
                continue
            label = f"{law + ' ' if law else ''}{section}".strip()
            if q and q not in label.lower():
                continue
            if label in seen:
                continue
            seen.add(label)
            out.append({"ref": pid, "label": label, "section": section, "law_book": law})
            if len(out) >= limit:
                break
        return out

    def get_parents(self, parent_ids: list[str]) -> list[dict]:
        """Volle Paragrafen zu parent_ids (für #-Mentions, garantierter Kontext)."""
        if not parent_ids or TABLE_NAME not in self.list_tables():
            return []
        table = self.db.open_table(TABLE_NAME)
        ids = ", ".join(f"'{_sql_escape(p)}'" for p in parent_ids if p)
        if not ids:
            return []
        try:
            rows = table.search().where(f"parent_id IN ({ids})").limit(2000).to_list()
        except Exception:
            return []
        seen, out = set(), []
        for r in rows:
            pid = r.get("parent_id")
            if pid in seen:
                continue
            seen.add(pid)
            out.append({
                "source": r.get("source", ""), "page": r.get("page", 1),
                "section": r.get("section", ""), "absatz": "",
                "law_book": r.get("law_book", ""),
                "text": r.get("parent_text") or r.get("text", ""),
                "parent_id": pid,
            })
        return out

    def get_document_text(self, source: str, matter_id: str | None = None) -> str:
        """Rekonstruiert den Klartext eines Dokuments aus den gespeicherten
        Paragrafen (für die Schnellansicht/Popup). Dedupliziert nach parent_id
        und behält die Einfügereihenfolge bei."""
        if TABLE_NAME not in self.list_tables():
            return ""
        table = self.db.open_table(TABLE_NAME)
        where = f"source = '{_sql_escape(source)}'"
        if matter_id:
            # Sichtbarkeit wie bei der Suche: eigene Akte ODER geteilte Kategorien
            # (allgemein/rechtsprechung). So sind zitierte Normen/Urteile sichtbar,
            # ohne Dokumente fremder Mandate zu leaken.
            where += (f" AND (matter_id = '{_sql_escape(matter_id)}'"
                      f" OR scope = 'allgemein' OR scope = 'rechtsprechung')")
        try:
            rows = table.search().where(where).limit(5000).to_list()
        except Exception:
            return ""
        seen, parts = set(), []
        for r in rows:
            pid = r.get("parent_id") or r.get("chunk_id")
            if pid in seen:
                continue
            seen.add(pid)
            parts.append((r.get("parent_text") or r.get("text", "")).strip())
        return "\n\n".join(p for p in parts if p)

    def get_stats(self, matter_id: str | None = None) -> dict:
        """Returns statistics, optionally restricted to a single matter."""
        if TABLE_NAME not in self.list_tables():
            return {"total_chunks": 0, "sources": [], "matters": []}

        table = self.db.open_table(TABLE_NAME)
        arrow_table = table.to_arrow()
        matter_col = (
            arrow_table["matter_id"].to_pylist()
            if "matter_id" in arrow_table.schema.names
            else [DEFAULT_MATTER] * len(arrow_table)
        )
        sources_col = arrow_table["source"].to_pylist()

        rows = zip(sources_col, matter_col)
        if matter_id:
            rows = [(s, m) for s, m in rows if m == matter_id]
        else:
            rows = list(rows)

        return {
            "total_chunks": len(rows),
            "sources": sorted(set(s for s, _ in rows)),
            # matters konsistent aus den gefilterten rows ableiten – sonst leakt
            # /api/documents?matter_id=X die Akten-IDs aller anderen Mandanten.
            "matters": sorted(set(m for _, m in rows if m)),
        }


DEFAULT_SYSTEM_PROMPT = (
    "You are Amicus AI, the law firm's precise legal assistant. If asked who you are, "
    "answer that you are Amicus AI. You are given factual context extracted from "
    "uploaded documents and a user query. You MUST answer the query using ONLY the facts "
    "present in the context. If the answer cannot be inferred from the context, state clearly "
    "that the information is not available in the uploaded documents. "
    "Cite your sources (e.g. [1], [2]) when using facts from specific chunks. "
    "When a context block specifies a 'Gesetz:'/'Norm:'/'relevanter Abs.', you MUST cite the "
    "exact law, article/section AND paragraph where applicable (e.g. 'Art. 59 Abs. 2 VO 1107/2009' "
    "or '§ 28 Abs. 2 BDSG'). NEVER invent or guess an article, paragraph or law. "
    "If no Norm is given for a fact, do not attribute it to a specific article. "
    "Placeholders like [[PERSON_1]] or [[EMAIL_1]] are pseudonyms; keep them VERBATIM "
    "and unchanged in your answer. Answer in the language of the question."
)


class LLMService:
    """Provider-agnostischer LLM-Dienst mit zentraler DSGVO-Pseudonymisierung.

    Egal ob Bedrock (Claude / Nova / Mistral), OpenAI oder lokales ATOM-LLM
    (Gemma): personenbezogene Daten werden vor dem Versand lokal pseudonymisiert
    und in der gestreamten Antwort wieder re-identifiziert.
    """

    def __init__(self, pseudonymize: bool = PSEUDONYMIZE):
        self.pseudonymize = pseudonymize

    def stream(self, query: str, context_chunks: list[dict], model_key: str,
               system_prompt: str | None = None, extra_user: str | None = None):
        """Streamt eine Antwort. `extra_user` erlaubt z.B. Web-Recherche-Befunde
        als zusätzlichen (bereits geprüften) Kontext."""
        meta = MODELS.get(model_key) or MODELS[DEFAULT_MODEL_KEY]

        # DSGVO Fail-closed: Namen/Orte werden NUR via NER (spaCy) erkannt. Fehlt das
        # Modell, würden Klarnamen an ein Cloud-Modell gehen. Dann lieber hart stoppen,
        # statt still Personenbezug zu übertragen (per ANWALT_REQUIRE_NER abschaltbar).
        require_ner = os.environ.get("ANWALT_REQUIRE_NER", "1") != "0"
        if self.pseudonymize and require_ner and meta.get("cloud") and not ner_available():
            yield ("[Abgebrochen: Die lokale Namens-Erkennung (NER) ist nicht verfügbar. "
                   "Aus Datenschutzgründen wird ohne sie keine Anfrage an ein Cloud-Modell "
                   "gesendet. Bitte das deutsche spaCy-Modell installieren oder ein lokales "
                   "Modell verwenden.]")
            return

        ps = Pseudonymizer() if self.pseudonymize else None

        context_str = ""
        for i, chunk in enumerate(context_chunks):
            sec = chunk.get("section") or ""
            head = f"Source: {chunk['source']}, Page: {chunk['page']}"
            if chunk.get("law_book"):
                head += f", Gesetz: {chunk['law_book']}"
            if sec:
                head += f", Norm: {sec}"
            if chunk.get("absatz"):
                head += f", relevanter {chunk['absatz']}"
            context_str += f"[{i+1}] ({head})\n{chunk['text']}\n\n"
        if extra_user:
            context_str += f"\n{extra_user}\n"

        safe_context = ps.pseudonymize(context_str) if ps else context_str
        safe_query = ps.pseudonymize(query) if ps else query
        system = system_prompt or DEFAULT_SYSTEM_PROMPT

        user_content = (f"CONTEXT:\n{safe_context}\n\nQUESTION: {safe_query}"
                        if context_str.strip() else safe_query)
        messages = [{"role": "user", "content": user_content}]

        feed = flush = None
        if ps:
            feed, flush = ps.stream_reidentifier()

        usage_sink: dict = {}
        try:
            provider, model_id = get_provider(model_key)
            for text in provider.stream(model_id, messages, system, usage_sink=usage_sink):
                yield feed(text) if feed else text
            if flush:
                tail = flush()
                if tail:
                    yield tail
        except Exception as e:
            yield f"\n[Fehler beim {meta['provider']}-Provider: {str(e)}]\n"
        finally:
            # Verbrauch (echte Tokens dieser App) pro Modell buchen – für System-Status.
            try:
                if usage is not None:
                    key = model_key if model_key in MODELS else DEFAULT_MODEL_KEY
                    usage.record(key, usage_sink.get("input_tokens", 0),
                                 usage_sink.get("output_tokens", 0))
            except Exception:
                pass

    # Unterstützte Zielsprachen (erweiterbar). Quelle wird automatisch erkannt.
    LANG_NAMES = {
        "de": "Deutsch", "en": "Englisch", "fr": "Französisch",
        "nl": "Niederländisch", "es": "Spanisch", "it": "Italienisch",
        "pl": "Polnisch", "pt": "Portugiesisch",
    }

    def translate(self, text: str, target_lang: str, model_key: str):
        """Übersetzt Text in die Zielsprache; Ausgangssprache wird automatisch
        erkannt (Default Deutsch). PII bleibt als Platzhalter erhalten."""
        code = str(target_lang or "de").lower()[:2]
        # Unbekannter Code -> sinnvoller Default statt unsinniger Zielsprache im Prompt.
        lang = self.LANG_NAMES.get(code, "Englisch")
        system = (
            f"Du bist ein juristischer Fachübersetzer. Erkenne die Ausgangssprache "
            f"des folgenden Textes automatisch und übersetze ihn präzise ins {lang}. "
            f"Ist der Text bereits auf {lang}, gib ihn unverändert zurück. "
            f"Bewahre juristische Fachterminologie. Platzhalter wie [[PERSON_1]] "
            f"unverändert übernehmen. Gib NUR die Übersetzung aus."
        )
        yield from self.stream(text, [], model_key, system_prompt=system)


# Rückwärtskompatibilität: alter Name -> neuer Dienst
class BedrockClient(LLMService):
    def stream_answer(self, query, context_chunks, model_id_or_key):
        key = model_id_or_key if model_id_or_key in MODELS else DEFAULT_MODEL_KEY
        yield from self.stream(query, context_chunks, key)


def print_banner():
    banner = """
   _                     _ _      _                    _   
  /_\  _ __   __      __| | |_   /_\   __ _  ___ _ __ | |_ 
 //_\\\\| '_ \  \ \ /\ / /| | __| //_\\\\ / _` |/ _ \ '_ \| __|
/  _  \ | | |  \ V  V / | | |_ /  _  \ (_| |  __/ | | | |_ 
\_/ \_/_| |_|   \_/\_/  |_|\__\_/ \_/\__, |\___|_| |_|\__|
                                     |___/                 
    Local LanceDB + Local Embeddings + AWS Bedrock
    """
    console.print(Panel(Text(banner, justify="center", style="bold green"), border_style="green"))


def get_all_files(path: str) -> list[str]:
    """Retrieves all supported files from a path (file or folder)."""
    supported_exts = [".pdf", ".docx", ".txt", ".md"]
    files = []
    
    if os.path.isfile(path):
        if os.path.splitext(path)[1].lower() in supported_exts:
            files.append(path)
    elif os.path.isdir(path):
        for root, _, filenames in os.walk(path):
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() in supported_exts:
                    files.append(os.path.join(root, filename))
    return files


def main():
    parser = argparse.ArgumentParser(description="Anwalt Agent RAG CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest document(s) into local vector database")
    ingest_parser.add_argument("path", type=str, help="Path to file or folder containing files (.pdf, .docx, .txt, .md)")
    ingest_parser.add_argument("--matter", type=str, default=DEFAULT_MATTER, help="Mandanten-/Akten-ID (Trennung der Daten)")
    ingest_parser.add_argument("--law-book", type=str, default="", help="Gesetzbuch/Verordnung für Zitate (z.B. 'VO 1107/2009', 'BDSG')")
    ingest_parser.add_argument("--scope", type=str, default="mandat",
                               choices=["allgemein", "rechtsprechung", "mandat"],
                               help="Kategorie: allgemein | rechtsprechung | mandat")

    # Chat command
    chat_parser = subparsers.add_parser("chat", help="Start interactive RAG chat session")
    chat_parser.add_argument("--matter", type=str, default=None, help="Auf eine Akte einschränken")

    # Delete command (DSGVO Art. 17)
    del_parser = subparsers.add_parser("delete", help="Delete a document or a whole matter")
    del_parser.add_argument("--source", type=str, default=None, help="Dateiname des zu löschenden Dokuments")
    del_parser.add_argument("--matter", type=str, default=None, help="Komplette Akte löschen / Dokument auf Akte einschränken")

    # List command
    subparsers.add_parser("list", help="List ingested files and stats")

    # Clear command
    subparsers.add_parser("clear", help="Clear the vector database")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Ingest / List / Clear setup
    if args.command == "ingest":
        files = get_all_files(args.path)
        if not files:
            console.print(f"[bold red]No supported files found at: {args.path}[/bold red]")
            return

        embedder = Embedder()
        db = VectorDatabase(embedder)

        parsed_docs = []
        for file in files:
            console.print(f"[blue]Parsing [bold]{os.path.basename(file)}[/bold]...[/blue]")
            parsed_docs.extend(DocumentProcessor.parse_file(file))

        if parsed_docs:
            db.insert_documents(parsed_docs, matter_id=args.matter, law_book=args.law_book, scope=args.scope)
        else:
            console.print("[bold red]No text extracted from documents.[/bold red]")

    elif args.command == "delete":
        embedder = Embedder()
        db = VectorDatabase(embedder)
        if args.source:
            db.delete_document(args.source, matter_id=args.matter)
        elif args.matter:
            db.delete_matter(args.matter)
        else:
            console.print("[bold red]Bitte --source <datei> oder --matter <akte> angeben.[/bold red]")

    elif args.command == "list":
        embedder = Embedder()
        db = VectorDatabase(embedder)
        stats = db.get_stats()
        
        console.print("\n[bold green]=== Local Vector Database Stats ===[/bold green]")
        console.print(f"Total Chunks: [bold]{stats['total_chunks']}[/bold]")
        console.print(f"Total Documents: [bold]{len(stats['sources'])}[/bold]")
        
        if stats["sources"]:
            table = Table(title="Ingested Documents", border_style="blue")
            table.add_column("Document Name", style="cyan")
            for src in stats["sources"]:
                table.add_row(src)
            console.print(table)
        console.print()

    elif args.command == "clear":
        embedder = Embedder()
        db = VectorDatabase(embedder)
        db.clear_database()

    elif args.command == "chat":
        print_banner()

        embedder = Embedder()
        db = VectorDatabase(embedder)
        llm = LLMService()

        # Check stats
        stats = db.get_stats()
        console.print(f"[bold blue]Loaded database with {stats['total_chunks']} chunks from {len(stats['sources'])} documents.[/bold blue]")
        if stats["total_chunks"] == 0:
            console.print("[bold yellow]The database is currently empty. Run 'uv run rag_agent.py ingest <file>' first.[/bold yellow]\n")

        # Choose model
        model_key = DEFAULT_MODEL_KEY
        model_keys = list(MODELS.keys())
        console.print(Panel(
            f"Active Model: [bold green]{model_key}[/bold green] ({MODELS[model_key]['label']})\n"
            f"Commands during chat:\n"
            f"  [cyan]/model[/cyan] - Nächstes Modell ({', '.join(model_keys)})\n"
            f"  [cyan]/list[/cyan]  - List active documents\n"
            f"  [cyan]/clear[/cyan] - Clear terminal screen\n"
            f"  [cyan]/exit[/cyan]  - Exit the chat",
            title="Session Started", border_style="blue"
        ))

        while True:
            try:
                user_input = Prompt.ask("\n[bold yellow]User[/bold yellow]")
                query = user_input.strip()

                if not query:
                    continue

                if query.lower() == "/exit":
                    console.print("[blue]Exiting chat. Bye![/blue]")
                    break

                if query.lower() == "/clear":
                    console.print("\033[H\033[J")  # ANSI clear screen
                    print_banner()
                    continue

                if query.lower() == "/list":
                    stats = db.get_stats()
                    console.print(f"[blue]Documents in database: {', '.join(stats['sources']) if stats['sources'] else 'None'}[/blue]")
                    continue

                if query.lower() == "/model":
                    idx = (model_keys.index(model_key) + 1) % len(model_keys)
                    model_key = model_keys[idx]
                    console.print(f"[bold green]Switched to Model: {model_key} ({MODELS[model_key]['label']})[/bold green]")
                    continue

                # RAG Retrieval
                results = db.search(query, limit=5, matter_id=getattr(args, "matter", None))

                if not results:
                    console.print("[bold red]Agent[/bold red] (no documents loaded):")
                    # Fallback to direct model invocation without context
                    response_generator = llm.stream(query, [], model_key)
                else:
                    # Show retrieved sources
                    console.print("\n[bold cyan]Context Citations:[/bold cyan]")
                    for i, r in enumerate(results):
                        console.print(f"  [{i+1}] {r['source']} (Page {r['page']})")
                    console.print()

                    console.print("[bold green]Agent[/bold green]: ", end="")
                    response_generator = llm.stream(query, results, model_key)

                # Stream Bedrock response using Rich Live
                with Live(Text("", style="green"), auto_refresh=False) as live:
                    full_text = ""
                    for text_chunk in response_generator:
                        full_text += text_chunk
                        live.update(Text(full_text, style="green"), refresh=True)
                print()

            except (KeyboardInterrupt, EOFError):
                console.print("\n[blue]Exiting chat. Bye![/blue]")
                break


if __name__ == "__main__":
    main()
