import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { fmtTime, modeLabel, providerName, VERDICT_LABEL } from '../util.js'

function useLoad(fn, deps) {
  const [state, set] = useState({ data: null, error: null, loading: true })
  useEffect(() => {
    let live = true
    set((s) => ({ ...s, loading: true }))
    fn().then((data) => live && set({ data, error: null, loading: false })).catch((e) => live && set({ data: null, error: e.message, loading: false }))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}

const Err = ({ msg }) => <p className="notice bad" role="alert">{msg}</p>

export function RepoDNA({ refreshKey }) {
  const { data, error, loading } = useLoad(api.dna, [refreshKey])
  return (
    <div className="page">
      <div className="page-head">
        <div><h2>Repository DNA</h2><p className="muted">What RepoMind currently understands — generated only from memories it holds.</p></div>
      </div>
      {error && <Err msg={error} />}
      {loading && <div className="skeleton"><div className="sk w90" /></div>}
      {data && (
        <>
          <div className="dna-top card">
            <div><div className="big-num">{data.team_rules}</div><div className="muted small">Team rules</div></div>
            <div className="grow">
              <div className="muted small">Technologies mentioned in memory</div>
              {data.technologies.length ? <ul className="chips">{data.technologies.map((t) => <li key={t} className="chip used">{t}</li>)}</ul> : <p className="small muted">None named yet.</p>}
            </div>
            <div className="muted small">Source: {providerName(data.provider)}</div>
          </div>
          <div className="dna-grid">
            {Object.entries(data.sections).map(([name, s]) => (
              <section key={name} className="card dna-card">
                <header><h4>{name}</h4><span className="count">{s.count}</span></header>
                {s.rules.length ? <ul className="plain">{s.rules.map((r) => <li key={r.id}>{r.text}</li>)}</ul> : <p className="small muted">Nothing learned yet.</p>}
                {s.count > s.rules.length && <p className="small muted">+{s.count - s.rules.length} more in Memory Bank</p>}
              </section>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export function History({ refreshKey, onOpen }) {
  const { data, error, loading } = useLoad(api.history, [refreshKey])
  return (
    <div className="page">
      <div className="page-head"><div><h2>Review History</h2><p className="muted">Every review RepoMind has run in this workspace.</p></div></div>
      {error && <Err msg={error} />}
      {loading && <div className="skeleton"><div className="sk w90" /></div>}
      {data && data.reviews.length === 0 && <div className="empty big"><p>No reviews yet. Run one from the Review screen.</p></div>}
      {data && data.reviews.length > 0 && (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>PR</th><th>When</th><th>Mode</th><th>Memory</th><th>Verdict</th><th>Issues</th><th>Memories used</th><th /></tr></thead>
            <tbody>
              {data.reviews.map((r) => (
                <tr key={r.review_id}>
                  <td className="strong">{r.pr_title}</td>
                  <td>{fmtTime(r.created_at)}</td>
                  <td>{modeLabel(r.review_mode)}</td>
                  <td>{r.memory_enabled ? <span className="badge memory sm">ON</span> : <span className="badge none sm">OFF</span>}</td>
                  <td>{r.verdict ? <span className={`vbadge ${r.verdict}`}>{VERDICT_LABEL[r.verdict] || r.verdict}</span> : '—'}</td>
                  <td>{r.issues_found}</td>
                  <td>{r.memories_used}</td>
                  <td><button className="btn ghost sm" onClick={() => onOpen(r.review_id)}>Open Review</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function Analytics({ refreshKey }) {
  const { data, error, loading } = useLoad(api.analytics, [refreshKey])
  const cards = data && [
    ['Total reviews', data.total_reviews], ['Memory-backed reviews', data.memory_backed_reviews], ['Rules taught', data.rules_taught],
    ['Memories recalled', data.memories_recalled], ['Accepted suggestions', data.accepted_suggestions], ['Rejected suggestions', data.rejected_suggestions],
  ]
  return (
    <div className="page">
      <div className="page-head"><div><h2>Memory Impact</h2><p className="muted">Counted from real events in this workspace. Nothing is estimated.</p></div></div>
      {error && <Err msg={error} />}
      {loading && <div className="skeleton"><div className="sk w90" /></div>}
      {data && (
        <>
          <div className="metrics">{cards.map(([label, v]) => <div key={label} className="card metric"><div className="big-num">{v}</div><div className="muted small">{label}</div></div>)}</div>
          <p className="muted small">“Memory-backed” means a review where at least one recalled memory was applied to a finding ({data.memory_backed_reviews} of {data.memory_enabled_reviews} memory-enabled reviews).</p>
        </>
      )}
    </div>
  )
}

export function Settings({ health, onSeed, onReset, onError }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const run = async (fn) => { setBusy(true); setMsg(null); try { setMsg(await fn()) } catch (e) { onError(e.message) } finally { setBusy(false) } }
  const h = health
  return (
    <div className="page">
      <div className="page-head"><div><h2>Settings</h2><p className="muted">Credentials live in backend/.env and are never sent to the browser.</p></div></div>
      <div className="card kvcard">
        <dl className="kv">
          <dt>Memory</dt><dd>{h ? h.memory_mode : '—'}</dd>
          <dt>Hindsight</dt><dd>{h ? (h.hindsight.connected ? `Connected · bank “${h.hindsight.bank_id}”` : h.hindsight.configured ? `Configured but unreachable — ${h.hindsight.error}` : 'Not configured (set HINDSIGHT_API_KEY)') : '—'}</dd>
          <dt>Review engine</dt><dd>{h ? (h.groq.configured ? `Groq · ${h.groq.model} · ${h.groq.status}` : 'Local deterministic engine (set GROQ_API_KEY to use Groq)') : '—'}</dd>
        </dl>
      </div>
      <div className="card">
        <h4>Workspace</h4>
        <div className="row gap wrap">
          <button className="btn memory" disabled={busy} onClick={() => run(async () => { const r = await onSeed(); return `Seeded ${r.seeded} team rules via ${providerName(r.provider)}.` })}>Seed team knowledge</button>
          <button className="btn bad" disabled={busy} onClick={() => { if (window.confirm('Clear local demo memory, history and analytics? Hindsight memory is not touched.')) run(async () => { const r = await onReset(); return r.message }) }}>Reset local demo state</button>
        </div>
        {msg && <p className="notice ok" role="status">{msg}</p>}
      </div>
    </div>
  )
}
