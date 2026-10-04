import { useEffect, useMemo, useRef, useState } from 'react'
import { ask, getJSON, searchRefs } from './api'
import { Answer } from './text'
import Filters from './Filters'
import ReferenceCard from './ReferenceCard'
import Reader from './Reader'

const EXAMPLES = [
  'What is the difference between the soul and the body?',
  'Why does the soul suffer in the material world?',
  'What is pure devotional service?',
  'How can one become free from the three modes of nature?',
  'What is the position of Krishna as the Supreme Personality of Godhead?',
  'What happens to the soul at the time of death?',
]

const SOURCE_LABEL = { books: 'Books', transcripts: 'Lectures', letters: 'Letters' }

export default function App() {
  const [status, setStatus] = useState(null)
  const [books, setBooks] = useState([])
  const [selected, setSelected] = useState([])
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState('search')
  const [retrieval, setRetrieval] = useState('semantic')
  const [k, setK] = useState(12)
  const [model, setModel] = useState('')
  const [state, setState] = useState({ phase: 'idle' })
  const [reader, setReader] = useState(null)
  const [activeRef, setActiveRef] = useState(null)
  const abortRef = useRef(null)
  const refsEl = useRef(null)

  useEffect(() => {
    getJSON('/api/books').then(setBooks).catch(() => {})
    const load = () => getJSON('/api/status').then(setStatus).catch(() => setStatus(null))
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (status && !model) setModel(status.llm_models.includes(status.default_llm) ? status.default_llm : status.llm_models[0] || '')
  }, [status, model])

  const run = async (q = query) => {
    q = q.trim()
    if (!q) return
    setQuery(q)
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setActiveRef(null)
    const started = performance.now()
    if (mode === 'search' || !status?.ollama) {
      setState({ phase: 'searching', q })
      try {
        const res = await searchRefs(q, selected, k, retrieval)
        setState({ phase: 'done', q, results: res.results, terms: res.terms, answer: '', tookMs: res.took_ms,
          note: mode === 'ask' ? 'Local LLM (Ollama) is not running — showing references only.' : null })
      } catch (e) {
        setState({ phase: 'error', q, error: String(e) })
      }
      return
    }
    setState({ phase: 'searching', q })
    let answer = ''
    try {
      await ask({
        q, books: selected, k, model, mode: retrieval, signal: ctrl.signal,
        on: {
          sources: (d) => setState({ phase: 'answering', q, results: d.results, terms: d.terms, contextCount: d.context_count, answer: '' }),
          token: (d) => {
            answer += d.t
            setState((s) => ({ ...s, answer }))
          },
          llm_error: (d) => setState((s) => ({ ...s, phase: 'done', note: d.message })),
          done: () => setState((s) => ({ ...s, phase: 'done', tookMs: Math.round(performance.now() - started) })),
        },
      })
    } catch (e) {
      if (e.name !== 'AbortError') setState((s) => ({ ...s, phase: 'error', error: String(e) }))
    }
  }

  const stop = () => {
    abortRef.current?.abort()
    setState((s) => ({ ...s, phase: 'done' }))
  }

  const cite = (n) => {
    setActiveRef(n)
    document.getElementById(`ref-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const results = useMemo(() => state.results || [], [state.results])
  const grouped = useMemo(() => {
    const g = {}
    for (const r of results) g[r.book_title] = (g[r.book_title] || 0) + 1
    return Object.entries(g).sort((a, b) => b[1] - a[1])
  }, [results])

  const semanticPct = status ? Math.round((100 * status.embedded) / status.chunks) : 0
  const hasResults = state.phase !== 'idle'

  return (
    <div className={`app ${hasResults ? 'has-results' : ''}`}>
      <header className="topbar">
        <a className="brand" href="/" onClick={(e) => { e.preventDefault(); setState({ phase: 'idle' }); setQuery('') }}>
          <img src="/lotus.svg" alt="" />
          <span>Vedabase <b>Wisdom Search</b></span>
        </a>
        <div className="status">
          {status ? (
            <>
              <span className="pill" title="Passages indexed for keyword search">{status.chunks.toLocaleString()} passages</span>
              <span className={`pill ${semanticPct < 100 ? 'warn' : 'ok'}`} title={`Semantic embeddings (${status.embed_model})`}>
                semantic {semanticPct}%
              </span>
              <span className={`pill ${status.ollama ? 'ok' : 'off'}`} title="Local LLM via Ollama">
                {status.ollama ? `LLM: ${model || 'no model pulled'}` : 'LLM offline'}
              </span>
            </>
          ) : (
            <span className="pill off">server offline</span>
          )}
        </div>
      </header>

      <section className="hero">
        {!hasResults && (
          <>
            <h1>Search the philosophy</h1>
            <p className="sub">Private, fully local search across Prabhupāda&apos;s books, lectures and letters — every answer backed by references.</p>
          </>
        )}
        <form className="searchbox" onSubmit={(e) => { e.preventDefault(); run() }}>
          <div className="modes" role="tablist">
            <button type="button" className={mode === 'search' ? 'on' : ''} onClick={() => setMode('search')}>Vector search</button>
            <button type="button" className={mode === 'ask' ? 'on' : ''} onClick={() => setMode('ask')}>RAG answer (LLM + references)</button>
          </div>
          <div className="modes retrieval" role="tablist" title="Retrieval method">
            {[['semantic', 'Semantic (vector)'], ['hybrid', 'Hybrid'], ['keyword', 'Keyword']].map(([key, label]) => (
              <button type="button" key={key} className={retrieval === key ? 'on' : ''} onClick={() => setRetrieval(key)}>{label}</button>
            ))}
          </div>
          <div className="inputrow">
            <svg viewBox="0 0 24 24" className="icon"><path d="M15.5 14h-.79l-.28-.27A6.5 6.5 0 1 0 9.5 16a6.47 6.47 0 0 0 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" /></svg>
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask a question, e.g. “What is the nature of the soul?” or look up “Bg 2.13”"
            />
            {state.phase === 'answering' || state.phase === 'searching' ? (
              <button type="button" className="go stop" onClick={stop}>Stop</button>
            ) : (
              <button className="go" disabled={!query.trim()}>{mode === 'ask' ? 'Ask' : 'Search'}</button>
            )}
          </div>
          <Filters books={books} selected={selected} setSelected={setSelected} k={k} setK={setK}
            models={status?.llm_models || []} model={model} setModel={setModel} showModel={mode === 'ask'} />
        </form>
        {!hasResults && (
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} onClick={() => run(ex)}>{ex}</button>
            ))}
          </div>
        )}
      </section>

      {hasResults && (
        <main className="results">
          {state.phase === 'error' && <div className="note error">{state.error}</div>}
          {mode === 'ask' && (state.answer || state.phase === 'answering' || (state.phase === 'searching' && status?.ollama)) && (
            <section className="answer card">
              <div className="answer-head">
                <h2>Answer</h2>
                <span className="muted">
                  {state.phase === 'answering' && !state.answer && `Reading ${state.contextCount} top passages…`}
                  {state.phase === 'searching' && 'Searching your library…'}
                  {state.phase === 'done' && state.answer && `Synthesized locally from references 1–${Math.min(state.contextCount || 0, results.length)}`}
                </span>
              </div>
              <div className="answer-body">
                <Answer text={state.answer || ''} onCite={cite} maxCite={state.contextCount || 0} />
                {state.phase === 'answering' && <span className="caret" />}
              </div>
              <p className="disclaimer">AI-generated summary — always verify against the cited references below.</p>
            </section>
          )}
          {state.note && <div className="note">{state.note}</div>}
          {retrieval !== 'keyword' && status && status.embedded < status.chunks && results.length > 0 && (
            <div className="note">Vector index is {semanticPct}% built ({status.embedded.toLocaleString()} of {status.chunks.toLocaleString()} passages, books first) — semantic results cover only the embedded part for now.</div>
          )}

          {state.phase === 'searching' && !results.length && <div className="loading"><span /><span /><span /></div>}

          {results.length > 0 && (
            <section className="refs" ref={refsEl}>
              <div className="refs-head">
                <h2>References <span className="count">{results.length}</span> <span className="muted">{{ semantic: 'by vector similarity', hybrid: 'vector + keyword (RRF)', keyword: 'by BM25 keyword match' }[retrieval]}</span></h2>
                <div className="groups">
                  {grouped.map(([title, n]) => (
                    <span key={title} className="chip">{title} · {n}</span>
                  ))}
                </div>
              </div>
              {results.map((r, i) => (
                <ReferenceCard key={r.doc_id} n={i + 1} r={r} terms={state.terms} active={activeRef === i + 1}
                  usedInAnswer={mode === 'ask' && i < (state.contextCount || 0)} onOpen={() => setReader({ id: r.doc_id, passages: r.passages })}
                  sourceLabel={SOURCE_LABEL[r.source]} />
              ))}
            </section>
          )}
          {state.phase === 'done' && !results.length && <div className="note">No passages found. Try different words or remove filters.</div>}
        </main>
      )}

      {reader && <Reader id={reader.id} passages={reader.passages} terms={state.terms}
        onClose={() => setReader(null)} onNavigate={(id) => setReader({ id, passages: [] })} />}

      <footer className="foot">Runs entirely on your computer · Content: your private Vedabase library</footer>
    </div>
  )
}
