import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = Path(os.environ.get("VEDABASE_RAW_DIR", ROOT / "raw"))
DATA_DIR = Path(os.environ.get("RAG_DATA_DIR", ROOT / "index"))
MODEL_CACHE = Path(os.environ.get("RAG_MODEL_CACHE", ROOT / "models"))
DB_PATH = DATA_DIR / "vedabase.sqlite"

EMBED_MODEL = os.environ.get("EMBED_MODEL", "snowflake/snowflake-arctic-embed-xs")
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "32"))

# Vector DB: embedded on-disk Qdrant by default; set QDRANT_URL (e.g. http://localhost:6333) to use a Qdrant server.
QDRANT_PATH = Path(os.environ.get("QDRANT_PATH", DATA_DIR / "qdrant"))
QDRANT_URL = os.environ.get("QDRANT_URL", "")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "vedabase_chunks")

# Cross-encoder reranker (fastembed). "" disables. BAAI/bge-reranker-base is more accurate but ~8x slower.
RERANK_MODEL = os.environ.get("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
RERANK_TOP = int(os.environ.get("RERANK_TOP", "40"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b")


def query_prefix(model: str) -> str:
    return "Represent this sentence for searching relevant passages: " if any(x in model.lower() for x in ("bge", "arctic")) else ""
