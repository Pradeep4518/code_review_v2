import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import presets from './presets.json'
import { MODES } from './util.js'
import CompareDelta from './components/CompareDelta.jsx'
import Impact from './components/Impact.jsx'
import DiffEditor from './components/DiffEditor.jsx'
import ReviewPanel from './components/ReviewPanel.jsx'
import TeachCard from './components/TeachCard.jsx'
import WhyModal from './components/WhyModal.jsx'
import MemoryBank from './components/MemoryBank.jsx'
import { Analytics, History, RepoDNA, Settings } from './components/Pages.jsx'

const NAV = [
  ['review', 'Review'], ['impact', 'Team Impact'], ['memory', 'Memory Bank'], ['dna', 'Repository DNA'],
  ['history', 'Review History'], ['analytics', 'Analytics'], ['settings', 'Settings'],
]

function Header({ health, latency }) {
  const hs = health?.hindsight
  const connected = hs?.connected
  const groq = health?.groq
  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo" aria-hidden="true" />
        <div>
          <div className="brand-name">RepoMind</div>
          <div className="brand-tag">The Self-Evolving Code Review Agent</div>
        </div>
      </div>
      <div className="status-row" role="status" aria-label="System status">
        <div className={`status ${connected ? 'good' : health ? 'warn' : ''}`} title={hs?.error || ''}>
          <span className="pulse" /><span className="s-label">{health ? (connected ? 'Hindsight' : 'Memory') : 'Memory'}</span>
          <strong>{health ? (connected ? 'CONNECTED' : 'DEMO MEMORY MODE') : '…'}</strong>
        </div>
        <div className={`status ${groq?.status === 'READY' ? 'good' : 'muted-s'}`}>
          <span className="pulse" /><span className="s-label">Groq</span>
          <strong>{groq ? (groq.status === 'NOT CONFIGURED' ? 'NOT CONFIGURED' : groq.status) : '…'}</strong>
        </div>
        <div className="status plain"><span className="s-label">Latency</span><strong>{latency != null ? `${latency} ms` : '—'}</strong></div>
        <div className="status plain"><span className="s-label">Memories</span><strong>{health ? health.memory_count : '—'}</strong></div>
      </div>
    </header>
  )
}

export default function App() {
  const [view, setView] = useState('review')
  const [health, setHealth] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [preset, setPreset] = useState(presets[0].id)
  const [title, setTitle] = useState(presets[0].title)
  const [diff, setDiff] = useState(presets[0].diff)
  const [mode, setMode] = useState('general')
  const [results, setResults] = useState({ plain: null, memory: null })
  const [loading, setLoading] = useState({ plain: false, memory: false })
  const [delta, setDelta] = useState(null)
  const [decisions, setDecisions] = useState({})
  const [why, setWhy] = useState(null)
  const [prefill, setPrefill] = useState(null)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)

  const bump = useCallback(() => setRefreshKey((k) => k + 1), [])
  const refreshHealth = useCallback(() => api.health().then(setHealth).catch(() => setHealth(null)), [])
  useEffect(() => { refreshHealth() }, [refreshHealth, refreshKey])
  useEffect(() => { if (error) { const t = setTimeout(() => setError(null), 7000); return () => clearTimeout(t) } }, [error])
  useEffect(() => { if (toast) { const t = setTimeout(() => setToast(null), 3500); return () => clearTimeout(t) } }, [toast])
  const fail = useCallback((m) => setError(m), [])

  const loadPreset = (id) => {
    const p = presets.find((x) => x.id === id)
    setPreset(id); setTitle(p.title); setDiff(p.diff); setResults({ plain: null, memory: null }); setDelta(null)
  }
  const resetDiff = () => { const p = presets.find((x) => x.id === preset); if (p) { setDiff(p.diff); setTitle(p.title) } }

  const run = async (which) => {
    const bypass = which === 'plain'
    setLoading((l) => ({ ...l, [which]: true }))
    setDelta(null)
    try {
      const res = await api.review({ code_diff: diff, pr_title: title, bypass_memory: bypass, review_mode: mode })
      setResults((r) => ({ ...r, [which]: res }))
      bump()
    } catch (e) { fail(e.message) } finally { setLoading((l) => ({ ...l, [which]: false })) }
  }
  // One request runs both reviews and returns what memory added (the "AI tools are generic" feature).
  const compare = async () => {
    setLoading({ plain: true, memory: true }); setDelta(null)
    try {
      const res = await api.compare({ code_diff: diff, pr_title: title, review_mode: mode })
      setResults({ plain: res.plain, memory: res.memory }); setDelta(res.delta); bump()
    } catch (e) { fail(e.message) } finally { setLoading({ plain: false, memory: false }) }
  }
  const busy = loading.plain || loading.memory
  const canRun = diff.trim().length > 0 && diff.length <= 20000

  const sendFeedback = async (payload, decision, review, issue) => {
    try {
      const res = await api.feedback(payload)
      if (issue) setDecisions((d) => ({ ...d, [`${review.review_id}:${issue.id}`]: decision }))
      bump()
      return res
    } catch (e) { fail(e.message); return null }
  }
  const onAccept = (issue, review) => sendFeedback({ review_id: review.review_id, issue_id: issue.id, feedback_type: 'accepted' }, 'accepted', review, issue)
  const onReject = (issue, review) => sendFeedback({ review_id: review.review_id, issue_id: issue.id, feedback_type: 'rejected' }, 'rejected', review, issue)
  const onTeachIssue = async (issue, review, note) => {
    const res = await sendFeedback({ review_id: review.review_id, issue_id: issue.id, feedback_type: 'helpful', teach_as_rule: true, comment: note }, 'taught', review, issue)
    if (res) setToast(res.warning || '✓ Memory retained — it will influence future reviews.')
    return !!res
  }
  const onReviewFeedback = async (review, type, comment, teach) => {
    const res = await sendFeedback({ review_id: review.review_id, feedback_type: type, comment, teach_as_rule: teach, category: 'Other' }, null, review, null)
    if (res?.taught) setToast('✓ Memory retained — it will influence future reviews.')
    return !!res
  }
  const onTeachException = (conflict, review) => {
    const a = review.memories.find((m) => m.id === conflict.memory_ids[0])
    const b = review.memories.find((m) => m.id === conflict.memory_ids[1])
    setPrefill({ rule: b ? `Exception: ${b.text}` : 'Exception: ', category: (b || a)?.category || 'Other', n: Date.now() })
    document.getElementById('teach')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const openHistory = async (id) => {
    try {
      const r = await api.reviewDetail(id)
      setTitle(r.pr_title); setDiff(r.code_diff); setMode(r.review_mode); setPreset('')
      setResults(r.memory_enabled ? { plain: null, memory: r } : { plain: r, memory: null })
      const d = {}
      for (const [iid, t] of Object.entries(r.feedback || {})) if (iid !== '_review') d[`${r.review_id}:${iid}`] = t === 'helpful' ? 'taught' : t
      setDecisions((x) => ({ ...x, ...d }))
      setDelta(null); setView('review')
    } catch (e) { fail(e.message) }
  }

  const seed = async () => { const r = await api.seed(); bump(); setToast(r.warning || `✓ Seeded ${r.seeded} team rules`); return r }
  const reset = async () => { const r = await api.resetDemo(); setResults({ plain: null, memory: null }); setDecisions({}); bump(); return r }

  const latency = useMemo(() => {
    const r = results.memory || results.plain
    return r ? (r.groq_latency_ms ?? r.latency_ms) : null
  }, [results])

  return (
    <div className="app">
      <Header health={health} latency={latency} />
      <div className="shell">
        <nav className="nav" aria-label="Main">
          {NAV.map(([id, label]) => (
            <button key={id} className={view === id ? 'on' : ''} onClick={() => setView(id)} aria-current={view === id ? 'page' : undefined}>{label}</button>
          ))}
        </nav>
        <main>
          {error && <div className="banner bad" role="alert"><span>{error}</span><button className="btn ghost xs" onClick={() => setError(null)} aria-label="Dismiss">✕</button></div>}
          {toast && <div className="toast" role="status">{toast}</div>}

          {view === 'review' && (
            <div className="review-grid">
              <div className="col">
                <section className="card input-card" aria-labelledby="pr-title">
                  <div className="card-head">
                    <h3 id="pr-title">Pull request</h3>
                    <div className="mode">
                      <label htmlFor="mode" className="sr">Review mode</label>
                      <select id="mode" value={mode} onChange={(e) => setMode(e.target.value)}>
                        {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                      </select>
                    </div>
                  </div>
                  <label htmlFor="prt">PR title</label>
                  <input id="prt" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="What does this PR do?" />
                  <div className="presets" role="group" aria-label="Presets">
                    {presets.map((p) => (
                      <button key={p.id} type="button" className={`pill ${preset === p.id ? 'on' : ''}`} aria-pressed={preset === p.id} onClick={() => loadPreset(p.id)}>{p.name}</button>
                    ))}
                  </div>
                  <label>Code diff</label>
                  <DiffEditor value={diff} onChange={(v) => { setDiff(v); setPreset('') }} onReset={resetDiff} focusLine={why?.issue.line} />
                  {diff.length > 20000 && <p className="notice bad">Diff is over the 20,000 character limit.</p>}
                  <div className="row gap wrap actions">
                    <button className="btn secondary" disabled={busy || !canRun} onClick={() => run('plain')}>{loading.plain ? 'Reviewing…' : 'Review Without Memory'}</button>
                    <button className="btn memory strong" disabled={busy || !canRun} onClick={() => run('memory')}>{loading.memory ? 'Recalling…' : 'Review With Hindsight'}</button>
                    <button className="btn ghost" disabled={busy || !canRun} onClick={compare}>Compare Reviews</button>
                  </div>
                </section>
                <TeachCard prefill={prefill} memoryCount={health?.memory_count ?? null}
                  onTaught={() => bump()} onSeeded={(r) => { bump(); setToast(r.warning || `✓ Seeded ${r.seeded} team rules`) }} onError={fail} />
              </div>
              <div className="col">
                {delta && <CompareDelta delta={delta} />}
                <ReviewPanel variant="plain" data={results.plain} loading={loading.plain} decisions={decisions}
                  onWhy={(issue, review) => setWhy({ issue, review })} onAccept={onAccept} onReject={onReject} onTeach={onTeachIssue} />
                <ReviewPanel variant="memory" data={results.memory} loading={loading.memory} decisions={decisions}
                  onWhy={(issue, review) => setWhy({ issue, review })} onAccept={onAccept} onReject={onReject} onTeach={onTeachIssue}
                  onTeachException={onTeachException} onReviewFeedback={onReviewFeedback} />
                <p className="closing">RepoMind doesn’t just review code. It remembers how your team builds software.</p>
              </div>
            </div>
          )}
          {view === 'impact' && <Impact refreshKey={refreshKey} onError={fail} onToast={setToast} />}
          {view === 'memory' && <MemoryBank refreshKey={refreshKey} onError={fail} onSeeded={seed} onChanged={refreshHealth} />}
          {view === 'dna' && <RepoDNA refreshKey={refreshKey} />}
          {view === 'history' && <History refreshKey={refreshKey} onOpen={openHistory} />}
          {view === 'analytics' && <Analytics refreshKey={refreshKey} />}
          {view === 'settings' && <Settings health={health} onSeed={seed} onReset={reset} onError={fail} />}
        </main>
      </div>
      {why && <WhyModal issue={why.issue} review={why.review} onClose={() => setWhy(null)} />}
    </div>
  )
}
