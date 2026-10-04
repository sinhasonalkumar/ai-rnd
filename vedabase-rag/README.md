# Vedabase Wisdom Search — local RAG pipeline + vector search

A private, fully local Retrieval-Augmented Generation (RAG) system over the offline
**Vedabase Original** export (23 books incl. Bhagavad-gītā, Śrīmad-Bhāgavatam,
Caitanya-caritāmṛta, plus ~6,400 letters and ~3,600 lectures/conversations). Nothing leaves your machine.

## Pipeline
```
Vedabase zip
  └─ corpus.py      parse JSONP export → documents (verse / chapter / letter / lecture) → sections (translation, purport, text)
  └─ chunking.py    token-aware HIERARCHICAL chunking with the embedding model's own tokenizer
                      parent  = paragraph-aligned block ≤ 1024 tokens   (LLM context)
                      child   = sentence-aligned ~256 tokens, 32 overlap (what is indexed)
  └─ indexer.py     SQLite: docs / sections (parents) / chunks (children) + FTS5 BM25 keyword index
                    fastembed ONNX embeddings (snowflake-arctic-embed-xs, 384-d) → Qdrant collection `vedabase_chunks`
                      payload: book, source, kind, doc_id, parent_id, section   (filters run inside Qdrant)
query
  └─ BM25 top-100  +  Qdrant vector top-100  +  exact refs ("Bg 2.13", "SB 1.2.6", "CC Madhya 20.108")
  └─ Reciprocal Rank Fusion → top 40 candidates
  └─ cross-encoder RERANK (fastembed: Xenova/ms-marco-MiniLM-L-6-v2; or BAAI/bge-reranker-base)
  └─ group by document → reference cards
  └─ RAG: parent sections of the best child chunks (small-to-big) → local LLM via Ollama → answer with [n] citations
```

- **Vector DB — Qdrant.** Embedded on-disk mode by default (`index/qdrant/`, no server, no Docker).
  For HNSW-indexed search and faster filtered queries, run a Qdrant server and set `QDRANT_URL`:
  `docker run -p 6333:6333 -v $PWD/index/qdrant_server:/qdrant/storage qdrant/qdrant` then `QDRANT_URL=http://localhost:6333 ./start.sh`.
  (Embedded mode is exact brute-force search — fine at this corpus size.)
- **UI** (React + Vite):
  - Vector search or RAG answer.
  - Semantic, Hybrid or Keyword retrieval, with reranking on or off.
  - A pipeline bar showing hits and timings per stage.
  - Per-card **Inspect pipeline**: BM25 rank, vector rank, cosine, RRF, rerank score, the child chunk and its parent context.
  - Clickable `[n]` citations and a full verse/purport/letter/lecture reader.

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
- embeds the child chunks into Qdrant

Embedding is the slow part and is **resumable**: press Ctrl+C at any time and later run `./setup.sh` or `./start.sh` again. Books are embedded first, then letters, then lectures.

**4. Start the app.**
```bash
./start.sh
```
Your browser opens at **http://localhost:8000**. If embedding isn't finished, the server keeps embedding into Qdrant in the background. The header shows "semantic N%", and you can follow progress with `tail -f index/embed.log`.

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
| Want to re-index from scratch | `./stop.sh && rm -rf index && ./start.sh` |
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
| `EMBED_MODEL` | `snowflake/snowflake-arctic-embed-xs` | any fastembed model (also the chunking tokenizer; changing it re-indexes) |
| `QDRANT_URL` | *(empty = embedded)* | use a Qdrant server, e.g. `http://localhost:6333` |
| `QDRANT_PATH` | `./index/qdrant` | embedded Qdrant storage |
| `RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-6-v2` | cross-encoder; `BAAI/bge-reranker-base` = better/slower; empty = off |
| `RERANK_TOP` | `40` | fused candidates sent to the reranker |
| `CHUNK_TOKENS` / `CHUNK_OVERLAP` / `PARENT_TOKENS` | `256` / `32` / `1024` | hierarchical chunk sizes (re-run `indexer.py --rebuild`) |
| `RAG_CONTEXT_CHARS` | `1800` | parent-context characters per source given to the LLM |
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
- `GET /api/search?q=...&mode=semantic|hybrid|keyword&rerank=true&books=bg,sb&k=12` — ranked references with per-stage `scores` (bm25_rank, vector_rank, similarity, rrf, rerank) and a `pipeline` summary
- `GET /api/chunk/{id}` — child chunk with its parent section and siblings
- `POST /api/ask` `{"q": "...", "books": [], "k": 12, "model": "qwen2.5:7b"}` — Server-Sent Events:
  `sources`, `token`, `llm_error`, `done`
- `GET /api/doc/{id}` — full verse/chapter/letter/lecture (e.g. `bg/2/13`, `sb/1/2/6`, `letters/<slug>`)
- `GET /api/books`, `GET /api/status`

## Layout
```
backend/corpus.py       parse the export into documents + sections
backend/chunking.py     token-aware hierarchical chunker (parents / children)
backend/indexer.py      build SQLite/FTS5 index, then embed into Qdrant (resumable)
backend/vectorstore.py  Qdrant collection, embedding worker, vector search
backend/app.py          FastAPI: BM25 + vector + RRF + rerank, small-to-big context, Ollama streaming, serves the UI
frontend/            React UI (Vite)
setup.sh / start.sh / stop.sh   macOS + Linux scripts (setup.ps1 / start.ps1 for Windows)
```
