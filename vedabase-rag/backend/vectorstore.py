"""Qdrant vector store for child chunks (embedded on-disk mode by default, or a Qdrant server via QDRANT_URL)."""
import json
import sqlite3
import threading
import time
import warnings

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client import models as M

from config import (DATA_DIR, DB_PATH, EMBED_BATCH, EMBED_MODEL, MODEL_CACHE, QDRANT_COLLECTION, QDRANT_PATH,
                    QDRANT_URL, query_prefix)

META_PATH = DATA_DIR / "vectors_meta.json"


def embed_text(ref, chapter, section, text):
    head = ref if not chapter or chapter in ref else f"{ref} — {chapter}"
    label = {"translation": "Translation", "purport": "Purport"}.get(section)
    return f"{head}{' (' + label + ')' if label else ''}\n{text}"


class VectorStore:
    def __init__(self):
        if QDRANT_URL:
            self.client = QdrantClient(url=QDRANT_URL, timeout=120)
            self.kind = f"Qdrant server ({QDRANT_URL})"
        else:
            QDRANT_PATH.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(QDRANT_PATH))
            self.kind = "Qdrant embedded"
        self.lock = threading.RLock()  # the embedded client is not thread-safe
        self._model = None
        self._model_lock = threading.Lock()
        self.embedded = self.count() if self._meta_matches() else 0

    @property
    def model(self):
        with self._model_lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(EMBED_MODEL, cache_dir=str(MODEL_CACHE))
            return self._model

    def _meta_matches(self, build_id=None):
        have = json.loads(META_PATH.read_text()) if META_PATH.exists() else {}
        if build_id is None and DB_PATH.exists():
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60) as db:
                build_id = dict(db.execute("SELECT key, value FROM meta")).get("build_id")
        return have.get("embed_model") == EMBED_MODEL and have.get("build_id") == build_id

    def count(self):
        with self.lock:
            if not self.client.collection_exists(QDRANT_COLLECTION):
                return 0
            return self.client.count(QDRANT_COLLECTION, exact=True).count

    def _ensure_collection(self, build_id, dim):
        with self.lock:
            exists = self.client.collection_exists(QDRANT_COLLECTION)
            if exists and self._meta_matches(build_id):
                return
            if exists:
                self.client.delete_collection(QDRANT_COLLECTION)
            self.client.create_collection(
                QDRANT_COLLECTION,
                vectors_config=M.VectorParams(size=dim, distance=M.Distance.COSINE),
                hnsw_config=M.HnswConfigDiff(m=16, ef_construct=128),
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # payload indexes are a no-op in embedded mode
                for field in ("book", "source", "kind"):
                    self.client.create_payload_index(QDRANT_COLLECTION, field, M.PayloadSchemaType.KEYWORD)
        META_PATH.write_text(json.dumps({"embed_model": EMBED_MODEL, "build_id": build_id, "dim": dim}))
        self.embedded = 0

    def embed_pending(self, stop=None, log=print):
        """Embed child chunks that are not yet in Qdrant (resumable: ids are sequential, books first)."""
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=60)
        meta = dict(db.execute("SELECT key, value FROM meta"))
        total = int(meta["chunk_count"])
        dim = len(next(iter(self.model.embed(["probe"]))))
        self._ensure_collection(meta["build_id"], dim)
        start = self.count()
        self.embedded = start
        if start >= total:
            log(f"[embed] complete ({total} chunks in {QDRANT_COLLECTION})")
            return
        step = EMBED_BATCH * 16
        t0 = time.time()
        for lo in range(start, total, step):
            if stop is not None and stop.is_set():
                log("[embed] paused")
                return
            rows = db.execute(
                "SELECT c.id, c.ref, d.chapter, c.section, c.text, d.book, d.source, d.kind, c.doc_id, c.parent_id "
                "FROM chunks c JOIN docs d ON d.id=c.doc_id WHERE c.id >= ? AND c.id < ? ORDER BY c.id",
                (lo, lo + step)).fetchall()
            texts = [embed_text(r[1], r[2], r[3], r[4]) for r in rows]
            emb = np.asarray(list(self.model.embed(texts, batch_size=EMBED_BATCH)), dtype=np.float32)
            emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9
            payloads = [{"book": r[5], "source": r[6], "kind": r[7], "doc_id": r[8], "parent_id": r[9],
                         "section": r[3]} for r in rows]
            with self.lock:
                self.client.upsert(QDRANT_COLLECTION, wait=True,
                                   points=M.Batch(ids=[r[0] for r in rows], vectors=emb.tolist(), payloads=payloads))
            hi = lo + len(rows)
            self.embedded = hi
            rate = (hi - start) / (time.time() - t0)
            log(f"[embed] {hi}/{total} chunks ({rate:.1f}/s, ~{(total - hi) / rate / 60:.0f} min left)")
        log("[embed] complete")

    def embed_query(self, q):
        v = np.asarray(next(iter(self.model.embed([query_prefix(EMBED_MODEL) + q]))), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def search(self, qv, books=None, limit=100):
        if not self.embedded:
            return []
        flt = M.Filter(must=[M.FieldCondition(key="book", match=M.MatchAny(any=list(books)))]) if books else None
        with self.lock:
            res = self.client.query_points(QDRANT_COLLECTION, query=qv.tolist(), query_filter=flt, limit=limit,
                                           with_payload=False, search_params=M.SearchParams(hnsw_ef=128))
        return [(int(p.id), float(p.score)) for p in res.points]

    def similarities(self, qv, ids):
        """Cosine similarity of the query to specific chunks (e.g. keyword-only hits), for the pipeline inspector."""
        if not ids or not self.embedded:
            return {}
        with self.lock:
            pts = self.client.retrieve(QDRANT_COLLECTION, ids=list(ids), with_vectors=True, with_payload=False)
        return {int(p.id): float(np.dot(np.asarray(p.vector, dtype=np.float32), qv)) for p in pts}

    def close(self):
        with self.lock:
            self.client.close()
