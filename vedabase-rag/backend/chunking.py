"""Token-aware hierarchical chunking.

Each document section (verse translation, purport, chapter text, letter, lecture) is split into
  parents  — paragraph-aligned blocks of up to PARENT_TOKENS tokens (context given to the LLM), and
  children — sentence-aligned windows of ~CHILD_TOKENS tokens with CHILD_OVERLAP tokens of overlap
             (what gets embedded and keyword-indexed).
Token counts come from the embedding model's own tokenizer, so children always fit the model window.
"""
import os
import re

CHILD_TOKENS = int(os.environ.get("CHUNK_TOKENS", "256"))
CHILD_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "32"))
PARENT_TOKENS = int(os.environ.get("PARENT_TOKENS", "1024"))

SENT_RE = re.compile(r"(?<=[.!?;:\"”’)])\s+(?=[\"“‘(\[A-Z0-9])")


def load_tokenizer(model_name, cache_dir):
    from fastembed import TextEmbedding

    tok = TextEmbedding(model_name, cache_dir=str(cache_dir)).model.tokenizer
    tok.no_truncation()
    tok.no_padding()
    return tok


class HierarchicalChunker:
    def __init__(self, tokenizer, child_tokens=CHILD_TOKENS, overlap=CHILD_OVERLAP, parent_tokens=PARENT_TOKENS):
        self.tok = tokenizer
        self.child = child_tokens
        self.overlap = overlap
        self.parent = parent_tokens

    def _units(self, text):
        """Paragraphs -> list of (paragraph_index, sentence_text, n_tokens); over-long sentences are cut on token offsets."""
        pars = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        sents = [(pi, s.strip()) for pi, p in enumerate(pars) for s in SENT_RE.split(p) if s.strip()]
        if not sents:
            return []
        encs = self.tok.encode_batch([s for _, s in sents], add_special_tokens=False)
        units = []
        for (pi, s), e in zip(sents, encs):
            n = len(e.ids)
            if n <= self.child:
                units.append((pi, s, n))
                continue
            step = self.child - self.overlap
            for lo in range(0, n, step):
                hi = min(lo + self.child, n)
                piece = s[e.offsets[lo][0]: e.offsets[hi - 1][1]].strip()
                if piece:
                    units.append((pi, piece, hi - lo))
                if hi == n:
                    break
        return units

    @staticmethod
    def _join(units):
        out, last_p = [], None
        for pi, s, _ in units:
            if last_p is not None:
                out.append("\n\n" if pi != last_p else " ")
            out.append(s)
            last_p = pi
        return "".join(out)

    def _children(self, units):
        children, cur, size = [], [], 0
        for u in units:
            if cur and size + u[2] > self.child:
                children.append(cur)
                keep, ksize = [], 0
                for prev in reversed(cur):  # carry trailing sentences as overlap
                    if ksize + prev[2] > self.overlap:
                        break
                    keep.insert(0, prev)
                    ksize += prev[2]
                cur, size = keep, ksize
            cur.append(u)
            size += u[2]
        if cur and (not children or cur != children[-1][-len(cur):]):
            if children and size < self.child // 4 and sum(x[2] for x in children[-1]) + size <= self.child * 1.25:
                children[-1] = children[-1] + [u for u in cur if u not in children[-1]]
            else:
                children.append(cur)
        return [(self._join(c), sum(x[2] for x in c)) for c in children]

    def split(self, text):
        """Return [(parent_text, parent_tokens, [(child_text, child_tokens), ...]), ...]."""
        units = self._units(text)
        parents, cur, size = [], [], 0
        for u in units:  # pack whole paragraphs into parents
            if cur and size + u[2] > self.parent and u[0] != cur[-1][0] or (cur and size + u[2] > self.parent * 1.5):
                parents.append(cur)
                cur, size = [], 0
            cur.append(u)
            size += u[2]
        if cur:
            parents.append(cur)
        return [(self._join(p), sum(x[2] for x in p), self._children(p)) for p in parents]
