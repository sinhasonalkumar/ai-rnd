#!/usr/bin/env bash
# One-time setup: Python env, frontend build, keyword index, then embeddings (resumable).
set -euo pipefail
cd "$(dirname "$0")"

ZIP="${1:-}"
if [ ! -f raw/index.json ]; then
  if [ -z "$ZIP" ]; then
    echo "Usage: ./setup.sh \"/path/to/Vedabase Original.zip\"   (or unzip it into ./raw yourself)"; exit 1
  fi
  mkdir -p raw && unzip -oq "$ZIP" -d raw
  # handle zips that contain a top-level folder
  if [ ! -f raw/index.json ]; then
    inner="$(dirname "$(find raw -name index.json -path '*/index.json' | head -1)")"
    [ -n "$inner" ] && mv "$inner"/* raw/ 
  fi
fi

PY="${PYTHON:-python3}"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r backend/requirements.txt

(cd frontend && npm install --silent && npm run build --silent)

if command -v ollama >/dev/null 2>&1; then
  ollama pull "${OLLAMA_MODEL:-qwen2.5:1.5b}" || true
else
  echo "NOTE: Ollama not found. Install from https://ollama.com for AI answers (search works without it)."
fi

cd backend && ../.venv/bin/python indexer.py
