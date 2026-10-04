import { useState } from 'react'
import { Highlight } from './text'

const SECTION = { translation: 'Translation', purport: 'Purport' }

function citation(r) {
  if (r.kind === 'verse') return `${r.ref} (${r.book_title}${r.chapter ? `, ${r.chapter}` : ''})`
  if (r.kind === 'chapter') return r.ref
  return `${r.book_title}: ${r.ref}`
}

export default function ReferenceCard({ n, r, terms, active, usedInAnswer, onOpen, sourceLabel }) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)
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
      </div>
    </article>
  )
}
