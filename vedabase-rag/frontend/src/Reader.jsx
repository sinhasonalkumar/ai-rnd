import { useEffect, useRef, useState } from 'react'
import { getJSON } from './api'
import { Highlight } from './text'

export default function Reader({ id, passages, terms, onClose, onNavigate }) {
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState(null)
  const [showSyn, setShowSyn] = useState(false)
  const body = useRef(null)

  useEffect(() => {
    setDoc(null)
    setError(null)
    getJSON(`/api/doc/${id}`).then(setDoc).catch((e) => setError(String(e)))
  }, [id])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    if (!doc || !body.current) return
    const hit = body.current.querySelector('.hit')
    if (hit) hit.scrollIntoView({ block: 'center' })
    else body.current.scrollTop = 0
  }, [doc])

  const snippets = (passages || []).map((p) => p.text.slice(0, 80))
  const isHit = (text) => snippets.some((s) => s && text.includes(s.slice(0, 60)))
  const paras = (text) =>
    text.split(/\n{2,}/).map((t, i) => (
      <p key={i} className={isHit(t) ? 'hit' : ''}><Highlight text={t} terms={terms} /></p>
    ))

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="reader">
        <div className="reader-head">
          <div>
            <div className="muted">{doc?.book_title}{doc?.kind === 'verse' && doc?.chapter ? ` · ${doc.chapter}` : ''}</div>
            <h2>{doc?.ref || '…'}</h2>
          </div>
          <div className="reader-nav">
            <button disabled={!doc?.prev} onClick={() => onNavigate(doc.prev)} title="Previous">‹ Prev</button>
            <button disabled={!doc?.next} onClick={() => onNavigate(doc.next)} title="Next">Next ›</button>
            <button className="close" onClick={onClose} title="Close (Esc)">✕</button>
          </div>
        </div>
        <div className="reader-body" ref={body}>
          {error && <div className="note error">{error}</div>}
          {!doc && !error && <div className="loading"><span /><span /><span /></div>}
          {doc?.kind === 'verse' && (
            <>
              {doc.content.devanagari && <pre className="deva">{doc.content.devanagari}</pre>}
              {doc.content.text && <pre className="translit">{doc.content.text}</pre>}
              {doc.content.synonyms && (
                <div className="syn">
                  <button className="link" onClick={() => setShowSyn((s) => !s)}>{showSyn ? 'Hide' : 'Show'} word-for-word synonyms</button>
                  {showSyn && <p>{doc.content.synonyms}</p>}
                </div>
              )}
              <h3>Translation</h3>
              <p className={`translation ${isHit(doc.content.translation) ? 'hit' : ''}`}>
                <Highlight text={doc.content.translation} terms={terms} />
              </p>
              {doc.content.purport && (
                <>
                  <h3>Purport</h3>
                  {paras(doc.content.purport)}
                </>
              )}
            </>
          )}
          {doc?.kind === 'chapter' && doc.content.blocks.map((b, i) => {
            if (b.type === 'heading') return <h3 key={i}>{b.text}</h3>
            if (b.type === 'verse') return <pre key={i} className="translit">{b.text}</pre>
            return <p key={i} className={isHit(b.text) ? 'hit' : ''}><Highlight text={b.text} terms={terms} /></p>
          })}
          {(doc?.kind === 'letter' || doc?.kind === 'transcript') && (
            <>
              <div className="docmeta">
                {doc.date && <span>{doc.date}</span>}
                {doc.location && <span>{doc.location}</span>}
                {doc.chapter && <span>{doc.chapter}</span>}
              </div>
              <div className="prose">{paras(doc.content.text)}</div>
            </>
          )}
        </div>
      </aside>
    </div>
  )
}
