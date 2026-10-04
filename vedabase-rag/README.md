# Vedabase Wisdom Search — local RAG

A private, fully local Retrieval-Augmented Generation (RAG) app over the offline
**Vedabase Original** export (23 books incl. Bhagavad-gītā, Śrīmad-Bhāgavatam,
Caitanya-caritāmṛta, plus ~6,400 letters and ~3,600 lectures/conversations).

- **Search**: hybrid retrieval = SQLite FTS5 keyword search (BM25, diacritic-insensitive)
  + dense semantic vectors (local ONNX model `snowflake/snowflake-arctic-embed-xs` via fastembed),
  fused with Reciprocal Rank Fusion. Direct verse lookups work too (`Bg 2.13`, `SB 1.2.6`, `CC Madhya 20.108`).
- **Answers**: a local LLM via [Ollama](https://ollama.com) writes an explanation that cites
  numbered references `[1]`, `[2]` drawn only from your library.
- **UI**: React (Vite) — answer panel with clickable citations, reference cards (verse, book,
  chapter, translation/purport), full-text reader with prev/next, book/source filters, copy quote + reference.
- Nothing leaves your machine. After the one-time model downloads, it works offline.

## Requirements
- Python 3.10+
- Node.js 18+ (only to build the UI)
- Optional: [Ollama](https://ollama.com) for AI answers (search works without it)

## Setup (once)

macOS / Linux:
```bash
./setup.sh "/path/to/Vedabase Original.zip"
```
Windows (PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 "C:\path\Vedabase Original.zip"
```
This unzips the export into `raw/`, installs Python deps, builds the UI, pulls the default
Ollama model, builds the keyword index (~10 s) and then computes embeddings for ~137k passages.
Embedding is **resumable** (Ctrl+C any time and re-run) and is done in order books → letters → lectures.
Speed is roughly 15/s on 2 CPU cores and 60+/s on an 8-core laptop.

If you received a prebuilt `index/` folder, just drop it into the project root and skip waiting.

## Run
```bash
./start.sh          # Windows: .\start.ps1
```
Open http://localhost:8000. The app is usable immediately after the keyword index exists;
semantic results improve as embeddings complete (shown as "semantic N%" in the header).

### Better answers
The default LLM `qwen2.5:1.5b` runs on almost any CPU. If you have 16 GB RAM or a GPU, use a larger model:
```bash
ollama pull qwen2.5:7b        # or llama3.1:8b
OLLAMA_MODEL=qwen2.5:7b ./start.sh
```
Any pulled model can also be chosen from the "Model" dropdown in the UI.

## Configuration (env vars)
| Variable | Default | Meaning |
|---|---|---|
| `VEDABASE_RAW_DIR` | `./raw` | unzipped export (contains `index.json`, `data/`) |
| `RAG_DATA_DIR` | `./index` | SQLite + embeddings output |
| `EMBED_MODEL` | `snowflake/snowflake-arctic-embed-xs` | any fastembed model (changing it re-embeds) |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | default answer model |
| `RAG_CONTEXT_CHUNKS` | `5` | passages given to the LLM |
| `RAG_MAX_ANSWER_TOKENS` | `450` | max length of generated answers |

## Development
```bash
# backend (auto-reload)
cd backend && ../.venv/bin/uvicorn app:app --reload --port 8000
# frontend dev server with /api proxy -> :8000
cd frontend && npm run dev      # http://localhost:5173
```

## API
- `GET /api/search?q=...&books=bg,sb&k=12` — ranked references with matching passages
- `POST /api/ask` `{"q": "...", "books": [], "k": 12, "model": "qwen2.5:7b"}` — Server-Sent Events:
  `sources`, `token`, `llm_error`, `done`
- `GET /api/doc/{id}` — full verse/chapter/letter/lecture (e.g. `bg/2/13`, `sb/1/2/6`, `letters/<slug>`)
- `GET /api/books`, `GET /api/status`

## Layout
```
backend/corpus.py    parse the export into documents + passages
backend/indexer.py   build SQLite/FTS5 index and embeddings (resumable)
backend/app.py       FastAPI: hybrid search, citations, Ollama streaming, serves the UI
frontend/            React UI (Vite)
```
