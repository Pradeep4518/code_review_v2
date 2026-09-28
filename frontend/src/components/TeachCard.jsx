import { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { CATEGORIES } from '../util.js'

export default function TeachCard({ prefill, memoryCount, onTaught, onSeeded, onError }) {
  const [rule, setRule] = useState('')
  const [category, setCategory] = useState('Architecture')
  const [owner, setOwner] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const ta = useRef(null)

  useEffect(() => {
    if (prefill) { setRule(prefill.rule); setCategory(prefill.category || 'Other'); setDone(null); ta.current?.focus() }
  }, [prefill])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await api.teach({ rule, category, owner, reason })
      setDone(res)
      setRule(''); setReason('')
      onTaught(res)
    } catch (err) { onError(err.message) } finally { setBusy(false) }
  }

  const seed = async () => {
    setBusy(true)
    try { const r = await api.seed(); onSeeded(r) } catch (err) { onError(err.message) } finally { setBusy(false) }
  }

  return (
    <section className="card teach" id="teach" aria-labelledby="teach-title">
      <div className="teach-head">
        <div>
          <h3 id="teach-title">Teach Hindsight</h3>
          <p className="muted small">Turn team knowledge into persistent engineering memory.</p>
        </div>
        {memoryCount === 0 && <button type="button" className="btn ghost sm" onClick={seed} disabled={busy}>Seed team knowledge</button>}
      </div>
      <form onSubmit={submit}>
        <label htmlFor="rule">Rule</label>
        <textarea id="rule" ref={ta} rows={3} value={rule} maxLength={1000} required minLength={8}
          onChange={(e) => { setRule(e.target.value); if (done) setDone(null) }}
          placeholder="e.g. All FastAPI route handlers must remain thin. Database access must never happen directly inside API routes." />
        <label htmlFor="why">Why does this rule exist? <span className="muted">(optional, kept so the knowledge outlives you)</span></label>
        <input id="why" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="e.g. A raw query in a route caused a data leak last year." />
        <div className="row gap wrap end">
          <div className="grow">
            <label htmlFor="owner">Taught by</label>
            <input id="owner" value={owner} maxLength={80} onChange={(e) => setOwner(e.target.value)} placeholder="Your name" />
          </div>
          <div className="grow">
            <label htmlFor="cat">Category</label>
            <select id="cat" value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
          <button className="btn memory" disabled={busy || rule.trim().length < 8}>{busy ? 'Retaining…' : 'Teach Hindsight'}</button>
        </div>
      </form>
      {done && (
        <div className="success" role="status">
          <strong>✓ {done.duplicate ? 'Already remembered' : 'Memory retained'}</strong>
          <p className="small">{done.duplicate ? 'This rule is already in memory.' : 'Memory will influence future reviews.'} Stored via {done.provider === 'hindsight' ? 'Hindsight' : 'demo memory (fallback)'}.</p>
          {done.warning && <p className="small">{done.warning}</p>}
        </div>
      )}
    </section>
  )
}
