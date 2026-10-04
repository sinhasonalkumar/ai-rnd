"""Build the local search index.

Stage 1 (minutes): parse the Vedabase export, split every section with token-aware hierarchical chunking
  (parents ~1024 tokens, children ~256 tokens / 32 overlap) into SQLite + an FTS5 keyword index.
Stage 2 (resumable): embed child chunks with a local ONNX model (fastembed) into a Qdrant collection.
The server works after stage 1; it also resumes stage 2 in the background by itself.

Usage:  python backend/indexer.py [--rebuild] [--no-embed]
"""
import argparse
import json
import sqlite3
import sys
import time
import uuid

from chunking import CHILD_OVERLAP, CHILD_TOKENS, PARENT_TOKENS, HierarchicalChunker, load_tokenizer
from config import DATA_DIR, DB_PATH, EMBED_MODEL, MODEL_CACHE, RAW_DIR
from corpus import iter_corpus

SCHEMA_VERSION = "2"
SOURCE_ORDER = {"books": 0, "letters": 1, "transcripts": 2}

SCHEMA = """
CREATE TABLE docs (
  id TEXT PRIMARY KEY, ord INTEGER, kind TEXT, source TEXT, book TEXT, book_title TEXT,
  ref TEXT, chapter TEXT, date TEXT, location TEXT, content TEXT
);
-- parent blocks: paragraph-aligned, up to PARENT_TOKENS; given to the LLM as context
CREATE TABLE sections (
  id INTEGER PRIMARY KEY, doc_id TEXT, section TEXT, pos INTEGER, tokens INTEGER, text TEXT
);
-- child chunks: sentence-aligned ~CHILD_TOKENS windows; embedded + keyword indexed
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, doc_id TEXT, parent_id INTEGER, section TEXT, pos INTEGER, tokens INTEGER,
  ref TEXT, text TEXT
);
CREATE INDEX chunks_doc ON chunks(doc_id);
CREATE INDEX chunks_parent ON chunks(parent_id);
CREATE INDEX sections_doc ON sections(doc_id);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  ref, text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def needs_rebuild():
    if not DB_PATH.exists():
        return True
    with sqlite3.connect(DB_PATH, timeout=120) as db:
        try:
            meta = dict(db.execute("SELECT key, value FROM meta"))
        except sqlite3.OperationalError:
            return True
    return meta.get("schema_version") != SCHEMA_VERSION or meta.get("tokenizer") != EMBED_MODEL


def build_text_index():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    t = time.time()
    chunker = HierarchicalChunker(load_tokenizer(EMBED_MODEL, MODEL_CACHE))
    items = sorted(iter_corpus(RAW_DIR), key=lambda dc: SOURCE_ORDER[dc[0]["source"]])
    pid = cid = 0
    for ord_, (doc, sections) in enumerate(items):
        db.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc["id"], ord_, doc["kind"], doc["source"], doc["book"], doc["book_title"], doc["ref"],
             doc.get("chapter", ""), doc.get("date", ""), doc.get("location", ""),
             json.dumps(doc["content"], ensure_ascii=False)),
        )
        cpos = 0
        for spos, (name, text) in enumerate(sections):
            for ptext, ptokens, children in chunker.split(text):
                db.execute("INSERT INTO sections VALUES (?,?,?,?,?,?)", (pid, doc["id"], name, spos, ptokens, ptext))
                for ctext, ctokens in children:
                    db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
                               (cid, doc["id"], pid, name, cpos, ctokens, doc["ref"], ctext))
                    cid += 1
                    cpos += 1
                pid += 1
        if ord_ % 5000 == 0 and ord_:
            print(f"[index] {ord_}/{len(items)} docs chunked", flush=True)
    db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("schema_version", SCHEMA_VERSION), ("build_id", uuid.uuid4().hex), ("tokenizer", EMBED_MODEL),
        ("chunk_count", str(cid)), ("section_count", str(pid)), ("child_tokens", str(CHILD_TOKENS)),
        ("child_overlap", str(CHILD_OVERLAP)), ("parent_tokens", str(PARENT_TOKENS)),
    ])
    db.commit()
    db.close()
    tmp.replace(DB_PATH)
    print(f"[index] {len(items)} docs -> {pid} parent sections -> {cid} child chunks; "
          f"keyword index built in {time.time() - t:.0f}s", flush=True)


def build_embeddings():
    from vectorstore import VectorStore
    try:
        vs = VectorStore()
    except RuntimeError as e:  # embedded Qdrant is locked by a running server
        if "already accessed" in str(e):
            print("[embed] The app server is running and embeds in the background - nothing to do here.")
            return
        raise
    try:
        vs.embed_pending(log=lambda m: print(m, flush=True))
    finally:
        vs.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="re-parse and re-chunk the corpus from scratch")
    ap.add_argument("--no-embed", action="store_true", help="only build the keyword index")
    args = ap.parse_args()
    if not (RAW_DIR / "index.json").exists():
        sys.exit(f"Vedabase export not found at {RAW_DIR} (expected index.json and data/). "
                 "Unzip 'Vedabase Original.zip' there or set VEDABASE_RAW_DIR.")
    if args.rebuild or needs_rebuild():
        build_text_index()
    if not args.no_embed:
        build_embeddings()


if __name__ == "__main__":
    main()
