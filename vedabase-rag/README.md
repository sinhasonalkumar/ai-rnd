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

## Run it on your Mac — step by step

Works on Apple Silicon (M1–M4) and Intel Macs, macOS 12+. Everything runs on your Mac; nothing is uploaded.

**1. Install the tools (one time).** Open **Terminal** (⌘-Space → "Terminal") and install [Homebrew](https://brew.sh) if you don't have it:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
Then install Python 3.12, Node.js and Ollama (the local LLM; it uses the Mac GPU via Metal):
```bash
brew install python@3.12 node ollama git
```
> The `python3` that ships with macOS is 3.9 — too old. `setup.sh` finds the Homebrew one automatically.
> Prefer the desktop app? Download Ollama from https://ollama.com/download instead of `brew install ollama`.

**2. Get the code.**
```bash
git clone https://github.com/sinhasonalkumar/ai-rnd.git
cd ai-rnd/vedabase-rag
```

**3. Run setup with your Vedabase export.** Use the path of the zip (drag the file from Finder into the Terminal window to paste its path).
If Safari already unzipped it, pass the folder instead.
```bash
./setup.sh ~/Downloads/"Vedabase Original.zip"
```
What setup does:
- checks Python/Node
- unpacks the export into `raw/`
- creates `.venv` and builds the React UI
- picks an LLM based on your RAM (`qwen2.5:7b` if 16 GB or more, else `qwen2.5:1.5b`), saves it in `.env` and pulls it
- builds the keyword index (seconds)
- embeds about 137k passages into the vector index

Embedding is the slow part and is **resumable**: press Ctrl+C at any time and later run `./setup.sh` or `./start.sh` again. Books are embedded first, then letters, then lectures.

**4. Start the app.**
```bash
./start.sh
```
Your browser opens at **http://localhost:8000**. If embedding isn't finished, it keeps running in the background. The header shows "semantic N%", and you can follow progress with `tail -f index/embed.log`.

**5. Use it.**
- **Vector search** tab with **Semantic (vector)** finds passages by meaning and shows a similarity score for each. Switch to **Hybrid** or **Keyword** to compare.
- The **RAG answer** tab writes an answer with the local LLM, citing `[1]`, `[2]`… Click a citation to jump to its reference.
- Exact references work too: `Bg 2.13`, `SB 1.2.6`, `CC Madhya 20.108`.
- Filter by Books, Lectures or Letters, or by a single book. Open **Read full verse & purport** for the full text.

**6. Stop / restart.** Press Ctrl+C in the Terminal (or run `./stop.sh`). Next time, just `cd ai-rnd/vedabase-rag && ./start.sh`.

### Troubleshooting (macOS)
| Problem | Fix |
|---|---|
| `permission denied: ./setup.sh` | `chmod +x setup.sh start.sh stop.sh` |
| `Python 3.10+ not found` | `brew install python@3.12`, then reopen Terminal |
| Header shows "LLM offline" | run `ollama serve` in another Terminal (or open the Ollama app), then `ollama pull qwen2.5:7b` |
| Port 8000 already in use | `PORT=8010 ./start.sh` |
| Want to re-embed from scratch | `./stop.sh && cd backend && ../.venv/bin/python indexer.py --rebuild` |
| Mac sleeps during embedding | `caffeinate -i ./start.sh` keeps it awake |

## Linux / Windows
Requirements: Python 3.10+, Node.js 18+, optionally [Ollama](https://ollama.com).
```bash
./setup.sh "/path/to/Vedabase Original.zip" && ./start.sh                      # Linux
```
```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1 "C:\path\Vedabase Original.zip"; .\start.ps1   # Windows
```
If you received a prebuilt `index/` folder, drop it into the project root to skip embedding.

### Better answers
The default LLM `qwen2.5:1.5b` runs on almost any CPU. If you have 16 GB RAM or a GPU, use a larger model:
```bash
ollama pull qwen2.5:7b        # or llama3.1:8b
OLLAMA_MODEL=qwen2.5:7b ./start.sh     # or edit OLLAMA_MODEL in .env
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
- `GET /api/search?q=...&mode=semantic|hybrid|keyword&books=bg,sb&k=12` — ranked references; each has `similarity` (cosine) and `match`
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
setup.sh / start.sh / stop.sh   macOS + Linux scripts (setup.ps1 / start.ps1 for Windows)
```
