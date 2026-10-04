const STAGES = [
  ['keyword_hits', 'BM25', 'keyword_ms', 'SQLite FTS5 keyword hits over child chunks'],
  ['vector_hits', 'Vector', 'vector_ms', 'Qdrant nearest-neighbour hits (cosine)'],
  ['fused', 'RRF fused', 'fusion_ms', 'Reciprocal Rank Fusion of keyword + vector (+ exact refs)'],
  ['reranked', 'Reranked', 'rerank_ms', 'Candidates rescored by the cross-encoder'],
]

export default function Pipeline({ p }) {
  if (!p) return null
  const t = p.timings || {}
  return (
    <div className="pipeline" title={`${p.vector_db}${p.reranker ? ` · reranker ${p.reranker}` : ''}`}>
      {STAGES.filter(([key]) => key !== 'reranked' || p.reranker).map(([key, label, tk, tip], i) => (
        <span key={key} className="stage" title={tip}>
          {i > 0 && <span className="arrow">→</span>}
          <b>{p[key]}</b> {label}
          {t[tk] != null && <small>{key === 'vector_hits' ? (t.embed_query_ms || 0) + t[tk] : t[tk]} ms</small>}
        </span>
      ))}
      {p.direct_refs > 0 && <span className="stage" title="Explicit verse references"><b>{p.direct_refs}</b> exact ref</span>}
    </div>
  )
}
