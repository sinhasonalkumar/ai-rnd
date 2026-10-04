"""Parse the offline Vedabase export into documents and retrieval chunks."""
import html
import json
import re
from pathlib import Path

TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.I)
WS_RE = re.compile(r"[ \t\u00a0]+")
SENT_RE = re.compile(r"(?<=[.!?\"”])\s+(?=[A-Z“\"(\[])")

CHUNK_TARGET = 1100
CHUNK_MAX = 1600


def clean(s: str) -> str:
    if not s:
        return ""
    s = BR_RE.sub("\n", s)
    s = TAG_RE.sub("", s)
    s = html.unescape(s)
    s = WS_RE.sub(" ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def load_jsonp(path: Path):
    s = path.read_text(encoding="utf-8")
    s = s[s.index("=") + 1 :].rstrip().rstrip(";")
    return json.loads(s)


def split_long(par: str):
    if len(par) <= CHUNK_MAX:
        return [par]
    out, cur = [], ""
    for sent in SENT_RE.split(par):
        if cur and len(cur) + len(sent) + 1 > CHUNK_TARGET:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
        while len(cur) > CHUNK_MAX:
            out.append(cur[:CHUNK_TARGET])
            cur = cur[CHUNK_TARGET:]
    if cur:
        out.append(cur)
    return out


def chunk_paragraphs(paragraphs):
    """Greedily pack paragraphs into ~CHUNK_TARGET-char chunks."""
    pieces = [p for par in paragraphs if par.strip() for p in split_long(par.strip())]
    chunks, cur = [], []
    size = 0
    for p in pieces:
        if cur and size + len(p) > CHUNK_TARGET:
            chunks.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(p)
        size += len(p) + 2
    if cur:
        tail = "\n\n".join(cur)
        if chunks and len(tail) < 250 and len(chunks[-1]) + len(tail) < CHUNK_MAX:
            chunks[-1] += "\n\n" + tail
        else:
            chunks.append(tail)
    return chunks


def chapter_title_lookup(book_meta):
    """Map toc keys to titles, including nested canto/section chapters."""
    titles = {}
    for item in book_meta.get("toc", []):
        titles[item["key"]] = item.get("title", "")
        for ch in item.get("chapters", []) or []:
            titles[f"{item['key']}/{ch['key']}"] = ch.get("title", "")
    return titles


def iter_corpus(raw_dir: Path):
    """Yield (doc, chunks) where doc is a dict and chunks is a list of (section, text)."""
    index = json.loads((raw_dir / "index.json").read_text(encoding="utf-8"))
    books = index["books"]
    data_dir = raw_dir / "data"

    for code, meta in books.items():
        path = data_dir / f"{code}.jsonp.js"
        if not path.exists():
            continue
        data = load_jsonp(path)
        book_title = meta["title"]
        ch_titles = chapter_title_lookup(meta)

        if "verses" in data:
            for key, langs in data["verses"].items():
                en = langs.get("en") or {}
                parts = key.split("/")
                ch_key = "/".join(parts[:-1])
                chapter = ch_titles.get(ch_key, "")
                if code == "bg" and not chapter:
                    chapter = index["meta"].get("bg_chapters", {}).get("en", {}).get(parts[0], "")
                translation = clean(en.get("translation", ""))
                purport = clean(en.get("purport", ""))
                doc = {
                    "id": f"{code}/{key}",
                    "kind": "verse",
                    "source": "books",
                    "book": code,
                    "book_title": book_title,
                    "ref": en.get("title", key),
                    "chapter": chapter,
                    "content": {
                        "devanagari": en.get("devanagari", ""),
                        "text": en.get("text", ""),
                        "synonyms": clean(en.get("synonyms", "")),
                        "translation": translation,
                        "purport": purport,
                    },
                }
                chunks = []
                if translation:
                    chunks.append(("translation", translation))
                for c in chunk_paragraphs(purport.split("\n\n") if purport else []):
                    chunks.append(("purport", c))
                if chunks:
                    yield doc, chunks
        elif "chapters" in data:
            for key, langs in data["chapters"].items():
                en = langs.get("en") or {}
                blocks = en.get("blocks", [])
                title = en.get("title") or ch_titles.get(key, key)
                paras = [
                    {"type": b["type"], "text": clean(b.get("html", ""))} for b in blocks
                ]
                doc = {
                    "id": f"{code}/{key}",
                    "kind": "chapter",
                    "source": "books",
                    "book": code,
                    "book_title": book_title,
                    "ref": f"{book_title}, {title}",
                    "chapter": title,
                    "content": {"blocks": paras},
                }
                texts = [p["text"] for p in paras if p["type"] != "heading"]
                chunks = [("text", c) for c in chunk_paragraphs(texts)]
                if chunks:
                    yield doc, chunks

    for kind, fname, label in (
        ("letter", "letters", "Letters"),
        ("transcript", "transcripts", "Lectures & Conversations"),
    ):
        path = data_dir / f"{fname}.jsonp.js"
        if not path.exists():
            continue
        for it in load_jsonp(path)["items"]:
            content = it.get("content", "")
            meta_bits = [b for b in (it.get("date"), it.get("location")) if b]
            title = it.get("title", it["slug"])
            doc = {
                "id": f"{fname}/{it['slug']}",
                "kind": kind,
                "source": fname,
                "book": fname,
                "book_title": label,
                "ref": title + (f" ({', '.join(meta_bits)})" if meta_bits else ""),
                "chapter": it.get("type") or it.get("letter_to") or "",
                "date": it.get("date", ""),
                "location": it.get("location", ""),
                "content": {"text": content},
            }
            chunks = [("text", c) for c in chunk_paragraphs(content.split("\n\n"))]
            if chunks:
                yield doc, chunks


def book_list(raw_dir: Path):
    index = json.loads((raw_dir / "index.json").read_text(encoding="utf-8"))
    out = [
        {"code": c, "title": m["title"], "abbrev": m.get("abbrev", ""), "source": "books"}
        for c, m in index["books"].items()
    ]
    out.append({"code": "letters", "title": "Letters", "abbrev": "Letter", "source": "letters"})
    out.append({"code": "transcripts", "title": "Lectures & Conversations", "abbrev": "Lecture", "source": "transcripts"})
    return out
