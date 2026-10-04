import { useState } from 'react'
import { getJSON } from './api'
import { Highlight } from './text'

const SECTION = { translation: 'Translation', purport: 'Purport' }

function citation(r) {
  if (r.kind === 'verse') return `${r.ref} (${r.book_title}${r.chapter ? `, ${r.chapter}` : ''})`
  if (r.kind === 'chapter') return r.ref
  return `${r.book_title}: ${r.ref}`
}

export default function ReferenceCard({ n, r, terms, active, usedInAnswer, onOpen, sourceLabel, reranked }) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [inspect, setInspect] = useState(false)
  const [parent, setParent] = useState(null)
  const toggleInspect = async () => {
    setInspect((v) => !v)
    if (!parent) {
      try { setParent(await getJSON(`/api/chunk/${r.passages[0].chunk_id}`)) } catch { setParent({ error: true }) }
    }
  }
  const s = r.scores || {}
  const fmt = (v, d = 3) => (v == null ? '—' : typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(d) : String(v))
  const copy = async () => {
    const p = r.passages[0]
    const text = `“${p.text}”\n— ${citation(r)}`
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  const passages = expanded ? r.passages : r.passages.slice(0, 1)
  return (
    <article id={`ref-${n}`} className={`ref card ${active ? 'active' : ''} src-${r.source}`}>
      <div className="ref-head">
        <span className={`num ${usedInAnswer ? 'used' : ''}`} title={usedInAnswer ? 'Used for the answer' : ''}>{n}</span>
        <div className="ref-title">
          <button className="reflink" onClick={onOpen}>{r.ref}</button>
          <div className="meta">
            <span className={`tag ${r.source}`}>{sourceLabel}</span>
            {r.kind === 'verse' && <span>{r.book_title}</span>}
            {r.chapter && r.kind !== 'chapter' && <span>{r.chapter}</span>}
          </div>
        </div>
        <div className="scores">
          {reranked && r.rerank != null && (
            <span className="sim rr" title="Cross-encoder relevance (0–1) — final ranking">
              <span className="bar"><span style={{ width: `${r.rerank * 100}%` }} /></span>
              rerank {r.rerank.toFixed(2)}
            </span>
          )}
          {r.similarity != null && (
            <span className="sim" title="Cosine similarity between your query and this passage embedding">
              <span className="bar"><span style={{ width: `${Math.max(0, Math.min(1, (r.similarity - 0.5) / 0.4)) * 100}%` }} /></span>
              {r.similarity.toFixed(3)}
            </span>
          )}
          <span className={`match ${r.match}`}>{r.match === 'both' ? 'semantic + keyword' : r.match}</span>
        </div>
      </div>
      {passages.map((p) => (
        <div key={p.chunk_id} className="passage">
          {SECTION[p.section] && <span className="section">{SECTION[p.section]}</span>}
          <p className={p.section === 'translation' ? 'tr' : ''}>
            <Highlight text={p.text} terms={terms} />
          </p>
        </div>
      ))}
      <div className="ref-actions">
        <button onClick={onOpen}>{r.kind === 'verse' ? 'Read full verse & purport' : r.kind === 'chapter' ? 'Read chapter' : 'Read full text'}</button>
        {r.passages.length > 1 && (
          <button onClick={() => setExpanded((e) => !e)}>{expanded ? 'Fewer passages' : `+${r.passages.length - 1} more passage`}</button>
        )}
        <button onClick={copy}>{copied ? 'Copied ✓' : 'Copy quote + reference'}</button>
        <button onClick={toggleInspect} className={inspect ? 'on' : ''}>{inspect ? 'Hide pipeline' : 'Inspect pipeline'}</button>
      </div>
      {inspect && (
        <div className="inspect">
          <table>
            <tbody>
              <tr><th>BM25 rank</th><td>{fmt(s.bm25_rank)}</td><th>Vector rank</th><td>{fmt(s.vector_rank)}</td></tr>
              <tr><th>Cosine similarity</th><td>{fmt(s.similarity)}</td><th>RRF score</th><td>{fmt(s.rrf, 5)}</td></tr>
              <tr><th>Rerank (cross-encoder)</th><td>{fmt(s.rerank)}</td><th>Final score</th><td>{fmt(r.score)}</td></tr>
              <tr><th>Matched child chunk</th><td>#{r.passages[0].chunk_id} · {r.passages[0].tokens} tokens</td>
                <th>Parent section</th><td>#{r.passages[0].parent_id} · {r.passages[0].parent_tokens} tokens{parent?.siblings ? ` · ${parent.siblings.length} children` : ''}</td></tr>
            </tbody>
          </table>
          {parent?.parent_text && (
            <details>
              <summary>Parent context sent to the LLM (small-to-big)</summary>
              <p className="parent-text"><Highlight text={parent.parent_text} terms={terms} /></p>
            </details>
          )}
        </div>
      )}
    </article>
  )
}
