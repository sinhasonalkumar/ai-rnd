#!/usr/bin/env bash
# Start the app at http://localhost:8000 . If embeddings are unfinished, indexing resumes in the background.
set -euo pipefail
cd "$(dirname "$0")/backend"
../.venv/bin/python - <<'PY' || (echo "Resuming embedding in background (see index/embed.log)"; nohup ../.venv/bin/python indexer.py >> ../index/embed.log 2>&1 &)
import sqlite3, sys
from config import DB_PATH, EMBED_MODEL
m = dict(sqlite3.connect(DB_PATH).execute("select key, value from meta"))
sys.exit(0 if m.get("embed_model") == EMBED_MODEL and m["embedded_upto"] == m["chunk_count"] else 1)
PY
echo "Open http://localhost:8000"
exec ../.venv/bin/uvicorn app:app --host 127.0.0.1 --port "${PORT:-8000}"
