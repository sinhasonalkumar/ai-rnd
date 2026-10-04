#!/usr/bin/env bash
# Start the app at http://localhost:8000 (macOS / Linux).
# If embeddings are unfinished, embedding resumes in the background (progress: index/embed.log).
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] && [ -f index/vedabase.sqlite ] || { echo "Run ./setup.sh first."; exit 1; }
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PORT="${PORT:-8000}"

# make sure Ollama is running (optional - search works without it)
if command -v ollama >/dev/null 2>&1 && ! curl -sf "${OLLAMA_URL:-http://127.0.0.1:11434}/api/version" >/dev/null 2>&1; then
  if [ "$(uname -s)" = "Darwin" ] && [ -d /Applications/Ollama.app ]; then open -a Ollama; else (nohup ollama serve >/dev/null 2>&1 &); fi
fi

cd backend
if ! ../.venv/bin/python - <<'PY'
import sqlite3, sys
from config import DB_PATH, EMBED_MODEL
m = dict(sqlite3.connect(DB_PATH).execute("select key, value from meta"))
sys.exit(0 if m.get("embed_model") == EMBED_MODEL and m.get("embedded_upto") == m.get("chunk_count") else 1)
PY
then
  if ! pgrep -f "python indexer.py" >/dev/null 2>&1; then
    echo "Embeddings incomplete - resuming in background (tail -f index/embed.log)"
    nohup ../.venv/bin/python indexer.py >> ../index/embed.log 2>&1 &
  fi
fi

URL="http://localhost:$PORT"
echo "Vedabase RAG running at $URL  (Ctrl+C to stop)"
if [ "$(uname -s)" = "Darwin" ] && [ -z "${NO_BROWSER:-}" ]; then (sleep 3; open "$URL") & fi
exec ../.venv/bin/uvicorn app:app --host 127.0.0.1 --port "$PORT"
