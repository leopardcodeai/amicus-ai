"""Gemeinsame Test-Konfiguration.

Setzt deterministische Umgebungs-Variablen, BEVOR die App-Module importiert
werden (mehrere Module lesen ENV beim Import). Tests laufen ohne AWS und ohne
echte Cloud-Aufrufe; schwere Modelle (Embedder/App) sind hinter @pytest.mark.slow.
"""
import os
import tempfile

# Feste Test-Umgebung: eigener API-Key, lokale Temp-Verzeichnisse, Auth aktiv.
os.environ.setdefault("ANWALT_API_KEY", "test-key-123")
os.environ.setdefault("ANWALT_DISABLE_AUTH", "0")
_TMP = tempfile.mkdtemp(prefix="amicus-tests-")
os.environ.setdefault("ANWALT_DB_DIR", os.path.join(_TMP, "lancedb"))
os.environ.setdefault("ANWALT_RAW_DIR", os.path.join(_TMP, "raw"))
os.environ.setdefault("ANWALT_AUDIT_LOG", os.path.join(_TMP, "audit.jsonl"))
os.environ.setdefault("ANWALT_APP_LOG", os.path.join(_TMP, "app.log"))
# AWS-Region gesetzt, damit Provider-Konstruktion nicht an fehlender Region scheitert.
os.environ.setdefault("AWS_DEFAULT_REGION", "eu-central-1")

TEST_API_KEY = os.environ["ANWALT_API_KEY"]
