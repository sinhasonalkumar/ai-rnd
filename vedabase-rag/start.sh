#!/usr/bin/env bash
# Start the app at http://localhost:8000 (macOS / Linux).
# If embeddings are unfinished, the server keeps embedding in the background (progress: index/embed.log).
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
# Rebuild the keyword/chunk index if it is missing or from an older version (vector embedding then
# continues inside the server in the background; progress: index/embed.log and the header "semantic N%").
../.venv/bin/python -c "import indexer, sys; sys.exit(1 if indexer.needs_rebuild() else 0)" || ../.venv/bin/python indexer.py --no-embed

URL="http://localhost:$PORT"
echo "Vedabase RAG running at $URL  (Ctrl+C to stop)"
if [ "$(uname -s)" = "Darwin" ] && [ -z "${NO_BROWSER:-}" ]; then (sleep 3; open "$URL") & fi
exec ../.venv/bin/uvicorn app:app --host 127.0.0.1 --port "$PORT"
