import { useEffect, useRef, useState } from 'react'

const GROUPS = [
  { key: 'books', label: 'Books' },
  { key: 'transcripts', label: 'Lectures & Conversations' },
  { key: 'letters', label: 'Letters' },
]

export default function Filters({ books, selected, setSelected, k, setK, models, model, setModel, showModel }) {
  const [open, setOpen] = useState(false)
  const panel = useRef(null)

  useEffect(() => {
    const onDoc = (e) => { if (panel.current && !panel.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const codesFor = (src) => books.filter((b) => b.source === src).map((b) => b.code)
  const allCodes = books.map((b) => b.code)
  const effective = selected.length ? selected : allCodes
  const groupState = (src) => {
    const codes = codesFor(src)
    const n = codes.filter((c) => effective.includes(c)).length
    return n === 0 ? 'none' : n === codes.length ? 'all' : 'some'
  }
  const normalize = (codes) => (codes.length === allCodes.length ? [] : codes)
  const toggleGroup = (src) => {
    const codes = codesFor(src)
    const st = groupState(src)
    const next = st === 'all' ? effective.filter((c) => !codes.includes(c)) : [...new Set([...effective, ...codes])]
    if (next.length) setSelected(normalize(next))
  }
  const toggleBook = (code) => {
    const next = effective.includes(code) ? effective.filter((c) => c !== code) : [...effective, code]
    if (next.length) setSelected(normalize(next))
  }
  const onlyBook = (code) => setSelected([code])

  const summary = selected.length === 0 ? 'Entire library'
    : selected.length === 1 ? books.find((b) => b.code === selected[0])?.title
      : `${selected.length} sources`

  return (
    <div className="filters">
      {GROUPS.map((g) => (
        <button type="button" key={g.key} className={`toggle ${groupState(g.key)}`} onClick={() => toggleGroup(g.key)}>
          <span className="box" />{g.label}
        </button>
      ))}
      <div className="dropdown" ref={panel}>
        <button type="button" className="toggle sel" onClick={() => setOpen((o) => !o)}>
          {summary} ▾
        </button>
        {open && (
          <div className="panel">
            <div className="panel-head">
              <b>Choose books</b>
              <button type="button" className="link" onClick={() => setSelected([])}>Select all</button>
            </div>
            <div className="booklist">
              {books.map((b) => (
                <label key={b.code} className="bookrow">
                  <input type="checkbox" checked={effective.includes(b.code)} onChange={() => toggleBook(b.code)} />
                  <span className="t">{b.title}</span>
                  <span className="muted">{b.docs.toLocaleString()}</span>
                  <button type="button" className="link only" onClick={(e) => { e.preventDefault(); onlyBook(b.code) }}>only</button>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>
      <label className="small">
        Refs
        <select value={k} onChange={(e) => setK(Number(e.target.value))}>
          {[6, 12, 20, 30, 50].map((n) => <option key={n}>{n}</option>)}
        </select>
      </label>
      {showModel && models.length > 0 && (
        <label className="small">
          Model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
      )}
    </div>
  )
}
