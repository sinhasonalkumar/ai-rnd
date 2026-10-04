"""Build the local search index.

Stage 1 (seconds): parse the Vedabase export into SQLite + FTS5 keyword index.
Stage 2 (resumable): compute dense embeddings with a local ONNX model (fastembed).
The server works after stage 1 and picks up embeddings as they are written.

Usage:  python backend/indexer.py [--rebuild] [--no-embed]
"""
import argparse
import json
import sqlite3
import sys
import time

import numpy as np

from config import DATA_DIR, DB_PATH, EMBED_BATCH, EMBED_MODEL, MODEL_CACHE, RAW_DIR, VEC_PATH
from corpus import iter_corpus

SOURCE_ORDER = {"books": 0, "letters": 1, "transcripts": 2}

SCHEMA = """
CREATE TABLE docs (
  id TEXT PRIMARY KEY, ord INTEGER, kind TEXT, source TEXT, book TEXT, book_title TEXT,
  ref TEXT, chapter TEXT, date TEXT, location TEXT, content TEXT
);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, doc_id TEXT, section TEXT, pos INTEGER, ref TEXT, text TEXT
);
CREATE INDEX chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  ref, text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build_text_index():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for p in (DB_PATH, VEC_PATH):
        p.unlink(missing_ok=True)
    tmp = DB_PATH.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    t = time.time()
    items = sorted(iter_corpus(RAW_DIR), key=lambda dc: SOURCE_ORDER[dc[0]["source"]])
    cid = 0
    for ord_, (doc, chunks) in enumerate(items):
        db.execute(
            "INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc["id"], ord_, doc["kind"], doc["source"], doc["book"], doc["book_title"], doc["ref"],
             doc.get("chapter", ""), doc.get("date", ""), doc.get("location", ""),
             json.dumps(doc["content"], ensure_ascii=False)),
        )
        for pos, (section, text) in enumerate(chunks):
            db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", (cid, doc["id"], section, pos, doc["ref"], text))
            cid += 1
    db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    db.executemany("INSERT INTO meta VALUES (?,?)", [
        ("embed_model", EMBED_MODEL), ("embedded_upto", "0"), ("chunk_count", str(cid)),
    ])
    db.commit()
    db.close()
    tmp.rename(DB_PATH)
    print(f"[index] {len(items)} docs, {cid} chunks, keyword index built in {time.time() - t:.0f}s", flush=True)


def embed_text(ref, chapter, section, text):
    head = ref if not chapter or chapter in ref else f"{ref} — {chapter}"
    label = {"translation": "Translation", "purport": "Purport"}.get(section)
    return f"{head}{' (' + label + ')' if label else ''}\n{text}"


def build_embeddings():
    from fastembed import TextEmbedding

    db = sqlite3.connect(DB_PATH)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    if meta.get("embed_model") != EMBED_MODEL:
        print(f"[embed] model changed ({meta.get('embed_model')} -> {EMBED_MODEL}); restarting embeddings")
        db.execute("UPDATE meta SET value=? WHERE key='embed_model'", (EMBED_MODEL,))
        db.execute("UPDATE meta SET value='0' WHERE key='embedded_upto'")
        db.commit()
        VEC_PATH.unlink(missing_ok=True)
        meta["embedded_upto"] = "0"
    total = int(meta["chunk_count"])
    start = int(meta["embedded_upto"])
    if start >= total:
        print("[embed] already complete")
        return
    model = TextEmbedding(EMBED_MODEL, cache_dir=str(MODEL_CACHE))
    dim = len(next(iter(model.embed(["probe"]))))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('embed_dim', ?)", (str(dim),))
    db.commit()
    mode = "r+" if VEC_PATH.exists() else "w+"
    vecs = np.memmap(VEC_PATH, dtype=np.float16, mode=mode, shape=(total, dim))
    step = EMBED_BATCH * 16
    t0, done0 = time.time(), start
    for lo in range(start, total, step):
        rows = db.execute(
            "SELECT c.id, c.ref, d.chapter, c.section, c.text FROM chunks c JOIN docs d ON d.id=c.doc_id "
            "WHERE c.id >= ? AND c.id < ? ORDER BY c.id", (lo, lo + step)).fetchall()
        texts = [embed_text(r[1], r[2], r[3], r[4]) for r in rows]
        emb = np.asarray(list(model.embed(texts, batch_size=EMBED_BATCH)), dtype=np.float32)
        emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9
        hi = lo + len(rows)
        vecs[lo:hi] = emb.astype(np.float16)
        vecs.flush()
        db.execute("UPDATE meta SET value=? WHERE key='embedded_upto'", (str(hi),))
        db.commit()
        rate = (hi - done0) / (time.time() - t0)
        eta = (total - hi) / rate / 60 if rate else 0
        print(f"[embed] {hi}/{total} chunks ({rate:.1f}/s, ~{eta:.0f} min left)", flush=True)
    print("[embed] complete")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="re-parse the corpus from scratch")
    ap.add_argument("--no-embed", action="store_true", help="only build the keyword index")
    args = ap.parse_args()
    if not (RAW_DIR / "index.json").exists():
        sys.exit(f"Vedabase export not found at {RAW_DIR} (expected index.json and data/). "
                 "Unzip 'Vedabase Original.zip' there or set VEDABASE_RAW_DIR.")
    if args.rebuild or not DB_PATH.exists():
        build_text_index()
    if not args.no_embed:
        build_embeddings()


if __name__ == "__main__":
    main()
