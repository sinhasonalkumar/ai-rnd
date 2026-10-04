"""Local RAG server for the Vedabase corpus (FastAPI).

Retrieval pipeline:  query -> [BM25 over child chunks (SQLite FTS5)] + [vector search (Qdrant)] + [explicit refs]
  -> Reciprocal Rank Fusion -> cross-encoder rerank (top RERANK_TOP) -> group by document
  -> LLM context = parent sections of the best child chunks (small-to-big).
"""
import asyncio
import json
import math
import os
import re
import sqlite3
import threading
import time

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import (DATA_DIR, DB_PATH, EMBED_MODEL, MODEL_CACHE, OLLAMA_MODEL, OLLAMA_URL, QDRANT_COLLECTION, RAW_DIR,
                    RERANK_MODEL, RERANK_TOP, ROOT)
from corpus import book_list
from vectorstore import VectorStore

CONTEXT_CHUNKS = int(os.environ.get("RAG_CONTEXT_CHUNKS", "5"))
CONTEXT_CHARS = int(os.environ.get("RAG_CONTEXT_CHARS", "1800"))
MAX_ANSWER_TOKENS = int(os.environ.get("RAG_MAX_ANSWER_TOKENS", "450"))
EMBED_IN_SERVER = os.environ.get("RAG_EMBED_IN_SERVER", "1") != "0"
FRONTEND_DIST = ROOT / "frontend" / "dist"
RRF_K = 60
CANDIDATES = 100
# Prefer primary books over lectures/letters when relevance is similar.
SOURCE_WEIGHT = {"books": 1.0, "transcripts": 0.9, "letters": 0.85}
STOPWORDS = set("""a an and are as at be by does do for from has have how i in is it its me my of on or
so that the their there these this to was what when where which who whom why will with you your
according explain tell about says say said describe meaning mean""".split())
WORD_RE = re.compile(r"[\w\u0100-\u024f\u1e00-\u1eff'-]+", re.U)
REF_RE = re.compile(
    r"\b(bg|b\.?g|gita|sb|bhag|cc|iso|isopanisad|noi)\.?\s*"
    r"(adi|madhya|antya)?\s*(\d+)(?:[.: ](\d+))?(?:[.: ](\d+))?\b", re.I)


class Reranker:
    def __init__(self, name):
        self.name = name
        self.model = None
        self.lock = threading.Lock()

    def score(self, q, texts):
        with self.lock:
            if self.model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self.model = TextCrossEncoder(self.name, cache_dir=str(MODEL_CACHE))
            return [1 / (1 + math.exp(-float(x))) for x in self.model.rerank(q, texts, batch_size=16)]


class Index:
    def __init__(self):
        if not DB_PATH.exists():
            raise SystemExit(f"Index not found at {DB_PATH}. Run: python backend/indexer.py")
        self.db_path = DB_PATH
        self.local = threading.local()
        self.meta = dict(self.db().execute("SELECT key, value FROM meta").fetchall())
        if self.meta.get("schema_version") != "2":
            raise SystemExit("Index is from an older version. Run: python backend/indexer.py --rebuild")
        self.chunk_count = int(self.meta["chunk_count"])
        self.vs = VectorStore()
        self.reranker = Reranker(RERANK_MODEL) if RERANK_MODEL else None
        self.stop = threading.Event()

    def db(self):
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False, timeout=30)
            conn.row_factory = sqlite3.Row
            self.local.conn = conn
        return conn

    @property
    def embedded(self):
        return self.vs.embedded

    def embed_worker(self):
        log_path = DATA_DIR / "embed.log"

        def log(msg):
            with open(log_path, "a") as f:
                f.write(msg + "\n")
        try:
            self.vs.embed_pending(stop=self.stop, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"[embed] error: {e!r}")

    # ---------- retrieval stages ----------
    def keyword_search(self, q, books, limit=CANDIDATES):
        terms = [t for t in WORD_RE.findall(q.lower()) if t not in STOPWORDS and len(t) > 1]
        if not terms:
            terms = [t for t in WORD_RE.findall(q.lower()) if len(t) > 1]
        if not terms or limit <= 0:
            return [], terms
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

    def search(self, q, books=None, k=12, per_doc=2, mode="hybrid", rerank=True):
        books = books or []
        timings, t = {}, time.perf_counter()

        def lap(name):
            nonlocal t
            now = time.perf_counter()
            timings[name] = round((now - t) * 1000)
            t = now

        kw, terms = self.keyword_search(q, books, 0 if mode == "semantic" else CANDIDATES)
        lap("keyword_ms")
        qv = self.vs.embed_query(q) if mode != "keyword" and self.embedded else None
        lap("embed_query_ms")
        vec_hits = self.vs.search(qv, books, CANDIDATES) if qv is not None else []
        lap("vector_ms")
        direct = self.direct_refs(q, books)

        kw_rank = {cid: i + 1 for i, cid in enumerate(kw)}
        vec_rank = {cid: i + 1 for i, (cid, _) in enumerate(vec_hits)}
        sims = dict(vec_hits)
        fused = {}
        for weight, ranked in ((1.0, kw), (1.2, [cid for cid, _ in vec_hits])):
            for rank, cid in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + weight / (RRF_K + rank + 1)
        n_fused = len(fused)
        if not fused and not direct:
            return [], terms, self._pipeline(kw, vec_hits, direct, 0, 0, False, timings)
        n_cand = max(RERANK_TOP, k * per_doc)
        cands = [cid for cid, _ in sorted(fused.items(), key=lambda x: -x[1])[:n_cand]]
        cands = list(dict.fromkeys(direct + cands))
        rows = {r["id"]: r for r in self.db().execute(
            f"SELECT c.id, c.doc_id, c.parent_id, c.section, c.pos, c.tokens, c.text, s.tokens AS parent_tokens, "
            f"d.kind, d.source, d.book, d.book_title, d.ref, d.chapter, d.date, d.location "
            f"FROM chunks c JOIN docs d ON d.id=c.doc_id JOIN sections s ON s.id=c.parent_id "
            f"WHERE c.id IN ({','.join('?' * len(cands))})", cands)}
        cands = [c for c in cands if c in rows]
        if qv is not None:
            missing = [c for c in cands if c not in sims]
            sims.update(self.vs.similarities(qv, missing))
        lap("fusion_ms")

        rr = {}
        use_rerank = bool(rerank and self.reranker and cands)
        if use_rerank:
            texts = [f"{rows[c]['ref']}\n{rows[c]['text']}" for c in cands]
            rr = dict(zip(cands, self.reranker.score(q, texts)))
        lap("rerank_ms")

        def final(cid):
            base = rr[cid] if use_rerank else fused.get(cid, 0.0) * 30
            return base * SOURCE_WEIGHT.get(rows[cid]["source"], 1.0) + (10.0 if cid in direct else 0.0)

        kw_set, vec_set = set(kw), set(vec_rank)
        results, by_doc = [], {}
        for cid in sorted(cands, key=final, reverse=True):
            r = rows[cid]
            passage = {"chunk_id": cid, "parent_id": r["parent_id"], "section": r["section"], "text": r["text"],
                       "tokens": r["tokens"], "parent_tokens": r["parent_tokens"]}
            if r["doc_id"] in by_doc:
                res = by_doc[r["doc_id"]]
                if len(res["passages"]) < per_doc:
                    res["passages"].append(passage)
                continue
            if len(results) >= k:
                continue
            res = {
                "doc_id": r["doc_id"], "kind": r["kind"], "source": r["source"], "book": r["book"],
                "book_title": r["book_title"], "ref": r["ref"], "chapter": r["chapter"], "date": r["date"],
                "location": r["location"], "score": round(final(cid), 4),
                "similarity": round(sims[cid], 4) if cid in sims else None,
                "rerank": round(rr[cid], 4) if cid in rr else None,
                "match": "reference" if cid in direct else "both" if cid in kw_set and cid in vec_set
                         else "semantic" if cid in vec_set else "keyword",
                "scores": {"bm25_rank": kw_rank.get(cid), "vector_rank": vec_rank.get(cid),
                           "rrf": round(fused.get(cid, 0.0), 5), "similarity": round(sims[cid], 4) if cid in sims else None,
                           "rerank": round(rr[cid], 4) if cid in rr else None},
                "passages": [passage],
            }
            by_doc[r["doc_id"]] = res
            results.append(res)
        return results, terms, self._pipeline(kw, vec_hits, direct, n_fused, len(cands), use_rerank, timings)

    def _pipeline(self, kw, vec_hits, direct, n_fused, n_cands, reranked, timings):
        return {"keyword_hits": len(kw), "vector_hits": len(vec_hits), "direct_refs": len(direct),
                "fused": n_fused, "reranked": n_cands if reranked else 0,
                "reranker": self.reranker.name if reranked else None, "vector_db": self.vs.kind,
                "timings": timings}

    def parent_context(self, passage, limit=CONTEXT_CHARS):
        """Small-to-big: return the parent section around the matched child chunk, trimmed to `limit` chars."""
        row = self.db().execute("SELECT text FROM sections WHERE id=?", (passage["parent_id"],)).fetchone()
        text = row["text"] if row else passage["text"]
        if len(text) <= limit:
            return text
        i = max(0, text.find(passage["text"][:80]))
        start = max(0, min(i - (limit - len(passage["text"])) // 2, len(text) - limit))
        return ("…" if start else "") + text[start:start + limit] + ("…" if start + limit < len(text) else "")

    def chunk(self, cid):
        r = self.db().execute(
            "SELECT c.id, c.doc_id, c.parent_id, c.section, c.pos, c.tokens, c.text, s.text AS parent_text, "
            "s.tokens AS parent_tokens FROM chunks c JOIN sections s ON s.id=c.parent_id WHERE c.id=?", (cid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["siblings"] = [dict(x) for x in self.db().execute(
            "SELECT id, pos, tokens FROM chunks WHERE parent_id=? ORDER BY pos", (r["parent_id"],))]
        return d

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


@app.on_event("startup")
async def startup():
    if EMBED_IN_SERVER and index.embedded < index.chunk_count:
        threading.Thread(target=index.embed_worker, daemon=True, name="embedder").start()
    await asyncio.to_thread(index.vs.embed_query, "warm up")
    if index.reranker:
        await asyncio.to_thread(index.reranker.score, "warm up", ["warm up"])


@app.on_event("shutdown")
def shutdown():
    index.stop.set()


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
        "sections": int(index.meta["section_count"]), "vector_db": index.vs.kind, "collection": QDRANT_COLLECTION,
        "reranker": RERANK_MODEL or None,
        "chunking": {k: int(index.meta[k]) for k in ("child_tokens", "child_overlap", "parent_tokens")},
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
           mode: str = Query("semantic", pattern="^(semantic|hybrid|keyword)$"), rerank: bool = True):
    t = time.time()
    results, terms, pipeline = index.search(q, _parse_books(books), k=min(k, 50), mode=mode, rerank=rerank)
    return {"query": q, "mode": mode, "rerank": rerank, "terms": terms, "results": results, "pipeline": pipeline,
            "semantic_coverage": round(index.embedded / index.chunk_count, 4),
            "took_ms": int((time.time() - t) * 1000)}


@app.get("/api/chunk/{chunk_id}")
def get_chunk(chunk_id: int):
    c = index.chunk(chunk_id)
    if not c:
        raise HTTPException(404, "not found")
    return c


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
    rerank: bool = True


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
        blocks.append(f"[{i}] {label}{' (' + section + ')' if section else ''}\n{index.parent_context(p)}")
    return "\n\n".join(blocks)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/ask")
async def ask(body: AskBody):
    mode = body.mode if body.mode in ("semantic", "hybrid", "keyword") else "semantic"
    results, terms, pipeline = await asyncio.to_thread(
        index.search, body.q, body.books, min(body.k, 50), 2, mode, body.rerank)
    model = body.model or OLLAMA_MODEL

    async def gen():
        yield sse("sources", {"results": results, "terms": terms, "pipeline": pipeline,
                              "context_count": min(len(results), CONTEXT_CHUNKS)})
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
