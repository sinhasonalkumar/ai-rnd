import { Fragment } from 'react'

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const fold = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()

/** Highlight query terms (diacritic-insensitive) inside plain text. */
export function Highlight({ text, terms }) {
  if (!terms?.length || !text) return text
  const folded = fold(text)
  if (folded.length !== text.length) {
    return <HighlightSimple text={text} terms={terms} />
  }
  const re = new RegExp(`\\b(${terms.map((t) => escapeRe(fold(t))).join('|')})\\w*`, 'gi')
  const out = []
  let last = 0
  let m
  while ((m = re.exec(folded))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    out.push(<mark key={m.index}>{text.slice(m.index, m.index + m[0].length)}</mark>)
    last = m.index + m[0].length
  }
  out.push(text.slice(last))
  return out
}

function HighlightSimple({ text, terms }) {
  const re = new RegExp(`(${terms.map(escapeRe).join('|')})`, 'gi')
  return text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : <Fragment key={i}>{part}</Fragment>))
}

/** Minimal markdown renderer for LLM answers, turning [n] into citation chips. */
export function Answer({ text, onCite, maxCite }) {
  const blocks = text.split(/\n{2,}/)
  return blocks.map((block, bi) => {
    const lines = block.split('\n').filter((l) => l.trim())
    if (lines.length && lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l))) {
      const ordered = /^\s*\d/.test(lines[0])
      const Tag = ordered ? 'ol' : 'ul'
      return (
        <Tag key={bi}>
          {lines.map((l, li) => (
            <li key={li}>{inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ''), onCite, maxCite)}</li>
          ))}
        </Tag>
      )
    }
    const h = block.match(/^#{1,4}\s+(.*)/)
    if (h) return <h4 key={bi}>{inline(h[1], onCite, maxCite)}</h4>
    return <p key={bi}>{inline(block, onCite, maxCite)}</p>
  })
}

function inline(s, onCite, maxCite) {
  const parts = s.split(/(\*\*[^*]+\*\*|\[\d+(?:\s*,\s*\d+)*\]|\*[^*]+\*)/g)
  return parts.map((p, i) => {
    if (!p) return null
    if (p.startsWith('**') && p.endsWith('**')) return <strong key={i}>{p.slice(2, -2)}</strong>
    const cite = p.match(/^\[(\d+(?:\s*,\s*\d+)*)\]$/)
    if (cite) {
      return cite[1].split(/\s*,\s*/).map((n) => {
        const num = Number(n)
        if (num < 1 || num > maxCite) return null
        return (
          <button key={`${i}-${n}`} className="cite" onClick={() => onCite(num)} title={`Go to reference ${n}`}>
            {n}
          </button>
        )
      })
    }
    if (p.startsWith('*') && p.endsWith('*') && p.length > 2) return <em key={i}>{p.slice(1, -1)}</em>
    return <Fragment key={i}>{p}</Fragment>
  })
}
