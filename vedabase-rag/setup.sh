#!/usr/bin/env bash
# One-time setup (macOS / Linux): checks prerequisites, unpacks the Vedabase export, creates the Python env,
# builds the React UI, pulls an Ollama model and builds the search index + embeddings (resumable).
# Usage: ./setup.sh "/path/to/Vedabase Original.zip"     (or a folder that already contains index.json)
set -euo pipefail
cd "$(dirname "$0")"

OS="$(uname -s)"
say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
hint() { if [ "$OS" = "Darwin" ]; then echo "  macOS: $1"; else echo "  Linux: $2"; fi; }

# ---------- 1. prerequisites ----------
say "Checking prerequisites"
PY=""
for c in ${PYTHON:-} python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ is required (the python3 that ships with macOS is 3.9)."
  hint "brew install python@3.12" "sudo apt install python3 python3-venv"
  fail "Python 3.10+ not found"
fi
echo "  python: $(command -v "$PY") ($("$PY" -V 2>&1))"

if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(+process.versions.node.split(".")[0] >= 18 ? 0 : 1)'; then
  echo "Node.js 18+ is required to build the UI."
  hint "brew install node" "see https://nodejs.org (or: sudo apt install nodejs npm)"
  fail "Node.js 18+ not found"
fi
echo "  node:   $(node -v)"
command -v unzip >/dev/null 2>&1 || fail "unzip not found"

# ---------- 2. corpus ----------
SRC="${1:-}"
if [ ! -f raw/index.json ]; then
  [ -n "$SRC" ] || fail "Pass the Vedabase export: ./setup.sh \"/path/to/Vedabase Original.zip\""
  [ -e "$SRC" ] || fail "Not found: $SRC"
  say "Unpacking corpus into ./raw"
  mkdir -p raw
  if [ -d "$SRC" ]; then
    # macOS Safari/Finder often auto-extracts zips into a folder
    cp -R "$SRC"/. raw/
  else
    unzip -oq "$SRC" -d raw
    rm -rf raw/__MACOSX
  fi
  if [ ! -f raw/index.json ]; then   # export wrapped in a top-level folder
    found="$(find raw -name index.json -not -path '*/__MACOSX/*' | head -1)"
    [ -n "$found" ] || fail "index.json not found inside $SRC - is this the Vedabase export?"
    inner="$(dirname "$found")"
    (shopt -s dotglob; mv "$inner"/* raw/)
  fi
fi
echo "  corpus: $(ls raw/data | wc -l | tr -d ' ') data files"

# ---------- 3. python env ----------
say "Installing Python dependencies (.venv)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r backend/requirements.txt

# ---------- 4. frontend ----------
say "Building the React UI"
(cd frontend && npm install --silent --no-audit --no-fund && npm run build --silent)

# ---------- 5. local LLM (optional) ----------
say "Local LLM (Ollama)"
if [ -z "${OLLAMA_MODEL:-}" ]; then
  MEM_GB=0
  if [ "$OS" = "Darwin" ]; then MEM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
  elif [ -r /proc/meminfo ]; then MEM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 )); fi
  if [ "$MEM_GB" -ge 16 ]; then OLLAMA_MODEL="qwen2.5:7b"; else OLLAMA_MODEL="qwen2.5:1.5b"; fi
  echo "  ${MEM_GB} GB RAM -> default model $OLLAMA_MODEL"
fi
printf 'OLLAMA_MODEL=%s\n' "$OLLAMA_MODEL" > .env
if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    if [ "$OS" = "Darwin" ] && [ -d /Applications/Ollama.app ]; then open -a Ollama; else (nohup ollama serve >/dev/null 2>&1 &); fi
    for _ in $(seq 1 20); do curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
  fi
  ollama pull "$OLLAMA_MODEL" || echo "  WARNING: could not pull $OLLAMA_MODEL (search still works; retry: ollama pull $OLLAMA_MODEL)"
else
  echo "  Ollama not installed - AI answers disabled, vector/keyword search still works."
  hint "brew install ollama   (or download the app from https://ollama.com/download)" "curl -fsSL https://ollama.com/install.sh | sh"
  echo "  Then run: ollama pull $OLLAMA_MODEL"
fi

# ---------- 6. index + embeddings ----------
say "Building keyword index and vector embeddings (resumable: Ctrl+C and re-run ./setup.sh or ./start.sh any time)"
cd backend && ../.venv/bin/python indexer.py

say "Done. Start the app with ./start.sh and open http://localhost:8000"
