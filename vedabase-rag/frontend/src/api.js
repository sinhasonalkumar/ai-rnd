export async function getJSON(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

export function searchRefs(q, books, k, mode, rerank) {
  const p = new URLSearchParams({ q, k: String(k), mode, rerank: String(rerank) })
  if (books.length) p.set('books', books.join(','))
  return getJSON(`/api/search?${p}`)
}

/** POST /api/ask and dispatch server-sent events to handlers. */
export async function ask({ q, books, k, model, mode, rerank, signal, on }) {
  const r = await fetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q, books, k, model, mode, rerank }),
    signal,
  })
  if (!r.ok || !r.body) throw new Error(`${r.status} ${r.statusText}`)
  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let event = 'message'
      let data = ''
      for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      on[event]?.(data ? JSON.parse(data) : null)
    }
  }
}
