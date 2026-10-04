"""Parse the offline Vedabase export into documents and retrieval chunks."""
import html
import json
import re
from pathlib import Path

# Documents are yielded with their top-level sections; chunking.HierarchicalChunker splits those
# into parent blocks and token-sized child chunks.

TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.I)
WS_RE = re.compile(r"[ \t\u00a0]+")



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


def chapter_title_lookup(book_meta):
    """Map toc keys to titles, including nested canto/section chapters."""
    titles = {}
    for item in book_meta.get("toc", []):
        titles[item["key"]] = item.get("title", "")
        for ch in item.get("chapters", []) or []:
            titles[f"{item['key']}/{ch['key']}"] = ch.get("title", "")
    return titles


def iter_corpus(raw_dir: Path):
    """Yield (doc, sections) where doc is a dict and sections is a list of (section_name, text)."""
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
                sections = [(name, txt) for name, txt in (("translation", translation), ("purport", purport)) if txt]
                if sections:
                    yield doc, sections
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
                if texts:
                    yield doc, [("text", "\n\n".join(texts))]

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
            text = clean(content) if "<" in content else content.strip()
            if text:
                yield doc, [("text", text)]


def book_list(raw_dir: Path):
    index = json.loads((raw_dir / "index.json").read_text(encoding="utf-8"))
    out = [
        {"code": c, "title": m["title"], "abbrev": m.get("abbrev", ""), "source": "books"}
        for c, m in index["books"].items()
    ]
    out.append({"code": "letters", "title": "Letters", "abbrev": "Letter", "source": "letters"})
    out.append({"code": "transcripts", "title": "Lectures & Conversations", "abbrev": "Lecture", "source": "transcripts"})
    return out
