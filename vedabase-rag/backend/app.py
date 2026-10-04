"""Local RAG server for the Vedabase corpus (FastAPI)."""
import asyncio
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import DB_PATH, EMBED_MODEL, MODEL_CACHE, OLLAMA_MODEL, OLLAMA_URL, RAW_DIR, ROOT, VEC_PATH, query_prefix
from corpus import book_list

CONTEXT_CHUNKS = int(os.environ.get("RAG_CONTEXT_CHUNKS", "5"))
MAX_ANSWER_TOKENS = int(os.environ.get("RAG_MAX_ANSWER_TOKENS", "450"))
FRONTEND_DIST = ROOT / "frontend" / "dist"
RRF_K = 60
# Prefer primary books over lectures/letters when relevance is similar.
SOURCE_WEIGHT = {"books": 1.0, "transcripts": 0.8, "letters": 0.75}
STOPWORDS = set("""a an and are as at be by does do for from has have how i in is it its me my of on or
so that the their there these this to was what when where which who whom why will with you your
according explain tell about says say said describe meaning mean""".split())
WORD_RE = re.compile(r"[\w\u0100-\u024f\u1e00-\u1eff'-]+", re.U)
REF_RE = re.compile(
    r"\b(bg|b\.?g|gita|sb|bhag|cc|iso|isopanisad|noi)\.?\s*"
    r"(adi|madhya|antya)?\s*(\d+)(?:[.: ](\d+))?(?:[.: ](\d+))?\b", re.I)


class Index:
    def __init__(self):
        if not DB_PATH.exists():
            raise SystemExit(f"Index not found at {DB_PATH}. Run: python backend/indexer.py")
        self.db_path = DB_PATH
        self.local = threading.local()
        db = self.db()
        self.chunk_count = int(db.execute("SELECT value FROM meta WHERE key='chunk_count'").fetchone()[0])
        book_codes = [r[0] for r in db.execute("SELECT DISTINCT book FROM docs")]
        self.book_ids = {b: i for i, b in enumerate(book_codes)}
        arr = np.zeros(self.chunk_count, dtype=np.int16)
        for cid, book in db.execute("SELECT c.id, d.book FROM chunks c JOIN docs d ON d.id=c.doc_id"):
            arr[cid] = self.book_ids[book]
        self.chunk_book = arr
        self.vecs = None
        self.embedded = 0
        self.model = None
        self.lock = threading.Lock()
        self.refresh_vectors()

    def db(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self.local.conn = conn
        return conn

    def refresh_vectors(self):
        meta = dict(self.db().execute("SELECT key, value FROM meta").fetchall())
        upto = int(meta.get("embedded_upto", 0))
        if upto <= self.embedded or not VEC_PATH.exists() or meta.get("embed_model") != EMBED_MODEL:
            return
        dim = int(meta["embed_dim"])
        mm = np.memmap(VEC_PATH, dtype=np.float16, mode="r", shape=(self.chunk_count, dim))
        vecs = np.asarray(mm[:upto], dtype=np.float32)
        with self.lock:
            self.vecs, self.embedded = vecs, upto

    def embed_query(self, q):
        if self.model is None:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(EMBED_MODEL, cache_dir=str(MODEL_CACHE))
        v = np.asarray(next(iter(self.model.embed([query_prefix(EMBED_MODEL) + q]))), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    # ---------- retrieval ----------
    def keyword_search(self, q, books, limit=150):
        terms = [t for t in WORD_RE.findall(q.lower()) if t not in STOPWORDS and len(t) > 1]
        if not terms:
            terms = [t for t in WORD_RE.findall(q.lower()) if len(t) > 1]
        if not terms:
            return [], []
        phrase = " ".join(terms)
        fts_q = " OR ".join(f'"{t}"' for t in terms)
        if len(terms) > 1:
            fts_q = f'(NEAR({" ".join(chr(34) + t + chr(34) for t in terms)}, 12)) OR {fts_q}'
        sql = ("SELECT f.rowid AS id FROM chunks_fts f JOIN chunks c ON c.id=f.rowid JOIN docs d ON d.id=c.doc_id "
               "WHERE chunks_fts MATCH ? ")
        params = [fts_q]
        if books:
            sql += f"AND d.book IN ({','.join('?' * len(books))}) "
            params += books
        sql += "ORDER BY bm25(chunks_fts, 3.0, 1.0) LIMIT ?"
        params.append(limit)
        try:
            rows = self.db().execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return [], terms
        return [r["id"] for r in rows], terms

    def vector_search(self, q, books, limit=150):
        """Return (ranked chunk ids, cosine similarity array over embedded chunks)."""
        with self.lock:
            vecs, n = self.vecs, self.embedded
        if vecs is None or n == 0:
            return [], None
        sims = vecs @ self.embed_query(q)
        scores = sims
        if books:
            ids = [self.book_ids[b] for b in books if b in self.book_ids]
            mask = np.isin(self.chunk_book[:n], ids)
            scores = np.where(mask, scores, -1.0)
        k = min(limit, n)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [int(i) for i in top if scores[i] > 0], sims

    def direct_refs(self, q, books=()):
        """Resolve explicit verse references like 'Bg 2.13', 'SB 1.2.6', 'CC Madhya 20.108'."""
        out = []
        for m in REF_RE.finditer(q):
            book, lila, a, b, c = m.groups()
            book = book.lower().replace(".", "")
            code = {"bg": "bg", "gita": "bg", "sb": "sb", "bhag": "sb", "cc": "cc", "iso": "iso",
                    "isopanisad": "iso", "noi": "noi"}[book]
            if books and code not in books:
                continue
            nums = [x for x in (a, b, c) if x]
            key = "/".join(([lila.lower()] if lila and code == "cc" else []) + nums)
            row = self.db().execute(
                "SELECT c.id FROM chunks c WHERE c.doc_id=? ORDER BY c.pos LIMIT 1", (f"{code}/{key}",)).fetchone()
            if row:
                out.append(row["id"])
        return out

    def search(self, q, books=None, k=12, per_doc=2, mode="hybrid"):
        books = books or []
        kw, terms = self.keyword_search(q, books) if mode != "semantic" else ([], self.keyword_search(q, [], 1)[1])
        vec, sims = self.vector_search(q, books) if mode != "keyword" else ([], None)
        kw_set, vec_set = set(kw), set(vec)
        direct = self.direct_refs(q, books)
        fused = {}
        for weight, ranked in ((1.0, kw), (1.2, vec)):
            for rank, cid in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + weight / (RRF_K + rank + 1)
        for cid in direct:
            fused[cid] = fused.get(cid, 0.0) + 1.0
        if not fused:
            return [], terms
        db = self.db()
        top_ids = [cid for cid, _ in sorted(fused.items(), key=lambda x: -x[1])[:400]]
        rows = {r["id"]: r for r in db.execute(
            f"SELECT c.id, c.doc_id, c.section, c.pos, c.text, d.kind, d.source, d.book, d.book_title, d.ref, "
            f"d.chapter, d.date, d.location FROM chunks c JOIN docs d ON d.id=c.doc_id "
            f"WHERE c.id IN ({','.join('?' * len(top_ids))})", top_ids)}
        ranked = sorted(((cid, fused[cid] * SOURCE_WEIGHT.get(rows[cid]["source"], 1.0)) for cid in rows),
                        key=lambda x: -x[1])
        results, by_doc = [], {}
        for cid, score in ranked[:400]:
            r = rows.get(cid)
            if r is None:
                continue
            if r["doc_id"] in by_doc:
                res = by_doc[r["doc_id"]]
                if len(res["passages"]) < per_doc:
                    res["passages"].append({"chunk_id": cid, "section": r["section"], "text": r["text"]})
                continue
            if len(results) >= k:
                continue
            res = {
                "doc_id": r["doc_id"], "kind": r["kind"], "source": r["source"], "book": r["book"],
                "book_title": r["book_title"], "ref": r["ref"], "chapter": r["chapter"], "date": r["date"],
                "location": r["location"], "score": round(score, 5),
                "similarity": round(float(sims[cid]), 4) if sims is not None and cid < len(sims) else None,
                "match": "both" if cid in kw_set and cid in vec_set else "semantic" if cid in vec_set
                         else "keyword" if cid in kw_set else "reference",
                "passages": [{"chunk_id": cid, "section": r["section"], "text": r["text"]}],
            }
            by_doc[r["doc_id"]] = res
            results.append(res)
        return results, terms

    def doc(self, doc_id):
        r = self.db().execute("SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["content"] = json.loads(d["content"])
        nb = self.db().execute(
            "SELECT (SELECT id FROM docs WHERE ord=?-1) AS prev, (SELECT id FROM docs WHERE ord=?+1) AS next",
            (d["ord"], d["ord"])).fetchone()
        d["prev"], d["next"] = nb["prev"], nb["next"]
        return d


index = Index()
app = FastAPI(title="Vedabase Local RAG")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


async def _refresher():
    while True:
        await asyncio.sleep(30)
        await asyncio.to_thread(index.refresh_vectors)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_refresher())
    await asyncio.to_thread(index.embed_query, "warm up")


async def ollama_models():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


@app.get("/api/status")
async def status():
    models = await ollama_models()
    return {
        "chunks": index.chunk_count, "embedded": index.embedded, "embed_model": EMBED_MODEL,
        "ollama": models is not None, "llm_models": models or [], "default_llm": OLLAMA_MODEL,
    }


@app.get("/api/books")
def books():
    counts = dict(index.db().execute("SELECT book, COUNT(*) FROM docs GROUP BY book").fetchall())
    return [{**b, "docs": counts.get(b["code"], 0)} for b in book_list(RAW_DIR) if b["code"] in counts]


def _parse_books(books: str | None):
    return [b for b in (books or "").split(",") if b]


@app.get("/api/search")
def search(q: str = Query(..., min_length=1), books: str | None = None, k: int = 12,
           mode: str = Query("semantic", pattern="^(semantic|hybrid|keyword)$")):
    t = time.time()
    results, terms = index.search(q, _parse_books(books), k=min(k, 50), mode=mode)
    return {"query": q, "mode": mode, "terms": terms, "results": results,
            "semantic_coverage": round(index.embedded / index.chunk_count, 4),
            "took_ms": int((time.time() - t) * 1000)}


@app.get("/api/doc/{doc_id:path}")
def get_doc(doc_id: str):
    d = index.doc(doc_id)
    if not d:
        raise HTTPException(404, "not found")
    return d


class AskBody(BaseModel):
    q: str
    books: list[str] = []
    k: int = 12
    model: str | None = None
    mode: str = "semantic"


SYSTEM_PROMPT = """You are a careful scholar of Vaishnava philosophy as presented in the books, lectures and letters \
of A.C. Bhaktivedanta Swami Prabhupada. Answer the user's question using ONLY the numbered source passages provided. \
Explain the philosophy clearly and concisely in 2-4 short paragraphs. After every claim, cite the supporting source(s) \
with bracketed numbers like [1] or [2][4]. Quote short key phrases from the sources where helpful. \
If the sources do not contain the answer, say so plainly. Do not invent verses, references or facts."""


def build_context(results):
    blocks = []
    for i, r in enumerate(results[:CONTEXT_CHUNKS], 1):
        p = r["passages"][0]
        label = f"{r['ref']}" + (f" — {r['book_title']}" if r["source"] == "books" and r["kind"] == "verse" else "")
        section = {"translation": "Translation", "purport": "Purport"}.get(p["section"], "")
        blocks.append(f"[{i}] {label}{' (' + section + ')' if section else ''}\n{p['text'][:1000]}")
    return "\n\n".join(blocks)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/ask")
async def ask(body: AskBody):
    mode = body.mode if body.mode in ("semantic", "hybrid", "keyword") else "semantic"
    results, terms = await asyncio.to_thread(index.search, body.q, body.books, min(body.k, 50), 2, mode)
    model = body.model or OLLAMA_MODEL

    async def gen():
        yield sse("sources", {"results": results, "terms": terms, "context_count": min(len(results), CONTEXT_CHUNKS)})
        if not results:
            yield sse("done", {"reason": "no_results"})
            return
        payload = {
            "model": model, "stream": True,
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": MAX_ANSWER_TOKENS},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Sources:\n\n{build_context(results)}\n\nQuestion: {body.q}\n\n"
                                            "Answer with citations like [1]:"},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=3)) as client:
                async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        msg = (await r.aread()).decode(errors="ignore")[:300]
                        yield sse("llm_error", {"message": f"Ollama error {r.status_code}: {msg}"})
                        return
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        tok = chunk.get("message", {}).get("content", "")
                        if tok:
                            yield sse("token", {"t": tok})
                        if chunk.get("done"):
                            break
        except httpx.ConnectError:
            yield sse("llm_error", {"message": f"Local LLM not reachable at {OLLAMA_URL}. Start Ollama to get "
                                               "synthesized answers; references are shown below."})
            return
        except Exception as e:  # noqa: BLE001
            yield sse("llm_error", {"message": str(e)})
            return
        yield sse("done", {"model": model})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = FRONTEND_DIST / path
        return FileResponse(f if path and f.is_file() else FRONTEND_DIST / "index.html")
