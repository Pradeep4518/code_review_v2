import { useState } from 'react'
import IssueCard from './IssueCard.jsx'
import { fmtTime, providerName } from '../util.js'

function Skeleton() {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Reviewing">
      <div className="sk w60" /><div className="sk w90" /><div className="sk w75" />
    </div>
  )
}

export default function ReviewPanel({ variant, data, loading, decisions, onWhy, onAccept, onReject, onTeach, onTeachException, onReviewFeedback }) {
  const memory = variant === 'memory'
  const [fbOpen, setFbOpen] = useState(false)
  const [fbComment, setFbComment] = useState('')
  const [fbSent, setFbSent] = useState(null)
  const [busy, setBusy] = useState(false)

  const sendFeedback = async (type, teach = false) => {
    setBusy(true)
    const ok = await onReviewFeedback(data, type, fbComment.trim(), teach)
    setBusy(false)
    if (ok) { setFbSent(type); setFbOpen(false); setFbComment('') }
  }

  return (
    <section className={`panel ${memory ? 'memory' : 'plain'}`} aria-label={memory ? 'Hindsight memory-augmented review' : 'Stateless generic review'}>
      <header className="panel-head">
        <div>
          <h3>{memory ? 'Hindsight Review' : 'Stateless Review'}</h3>
          <p className="muted small">{memory ? 'Memory-augmented — recalls what your team has taught' : 'Generic — knows nothing about your team'}</p>
        </div>
        <span className={`badge ${memory ? 'memory' : 'none'}`}>{memory ? 'MEMORY ENABLED' : 'NO MEMORY'}</span>
      </header>

      {loading && <Skeleton />}

      {!loading && !data && (
        <div className="empty">
          <p>{memory ? 'Run “Review With Hindsight” to see which team memories change the review.' : 'Run “Review Without Memory” to see what a generic AI reviewer says.'}</p>
        </div>
      )}

      {!loading && data && (
        <div className="panel-body">
          <div className="summary">
            <p>{data.review}</p>
            <div className="meta small muted">
              <span>{data.review_provider === 'groq' ? `Groq · ${data.model}` : 'Local review engine (no LLM)'}</span>
              {data.groq_latency_ms != null && <span>{data.groq_latency_ms} ms</span>}
              <span>{data.latency_ms} ms total</span>
              {memory && <span>Memory: {providerName(data.memory_provider)}</span>}
            </div>
            {data.warnings?.map((w, i) => <p key={i} className="notice warn">{w}</p>)}
          </div>

          {data.verdict && (
            <div className={`verdict ${data.verdict.status}`} role="status">
              <strong>{data.verdict.label}</strong>
              <span>{data.verdict.reason}</span>
              <span className="verdict-time" title="Estimate: about 5 min to read a PR plus 5 min per issue a senior reviewer no longer has to find">≈ {data.time_saved_min} min of senior review time saved</span>
            </div>
          )}
          {data.consistency?.status === 'consistent' && <p className="consist ok small">✓ Consistent: this code was reviewed before and the findings match.</p>}
          {data.consistency?.status === 'differs' && (
            <p className="consist warn small">⚠ Differs from the last review of this code.
              {data.consistency.added.length > 0 && <> New: {data.consistency.added.join('; ')}.</>}
              {data.consistency.missing.length > 0 && <> No longer flagged: {data.consistency.missing.join('; ')}.</>}
            </p>
          )}

          {memory && (
            <div className="recall">
              <div className="recall-head"><strong>{data.memory_count}</strong> {data.memory_count === 1 ? 'memory' : 'memories'} recalled · <strong>{data.memories_used.length}</strong> applied</div>
              {data.memories.length > 0 && (
                <ul className="chips">
                  {data.memories.map((m) => (
                    <li key={m.id} className={`chip ${m.used ? 'used' : ''}`} title={m.text}>
                      <span className="dot" />{m.label || m.category}{m.used ? ' · applied' : ''}
                    </li>
                  ))}
                </ul>
              )}
              {data.memory_count === 0 && <p className="small muted">Nothing relevant in memory yet. Teach Hindsight a rule or seed team knowledge, then review again.</p>}
            </div>
          )}

          {memory && data.scorecard?.total > 0 && (
            <details className="scorecard">
              <summary>
                <strong>Team standards checklist</strong>
                <span className={`sc-score ${data.scorecard.violated ? 'bad' : 'ok'}`}>{data.scorecard.score}% compliant · {data.scorecard.violated} of {data.scorecard.total} rules broken</span>
              </summary>
              <div className="sc-bar"><span style={{ width: `${data.scorecard.score}%` }} /></div>
              <ul className="sc-list">
                {data.scorecard.items.map((x) => <li key={x.id} className={x.status}><span aria-hidden="true">{x.status === 'violated' ? '✕' : '✓'}</span> {x.text}</li>)}
              </ul>
              <p className="small muted">Every reviewer and every run is held to the same recalled rules.</p>
            </details>
          )}

          {memory && data.conflicts.map((c, i) => (
            c.resolution === 'newest_wins' ? (
              <div key={i} className="conflict resolved" role="status">
                <div className="conflict-title">Conflict resolved: newest rule applied</div>
                {c.memory_ids.map((id, k) => {
                  const m = data.memories.find((x) => x.id === id)
                  return m ? (
                    <p key={id} className={`conflict-rule ${id === c.loser_id ? 'set-aside' : ''}`}>
                      <strong>{id === c.winner_id ? 'Applied' : 'Set aside'}</strong> “{m.text}”{fmtTime(m.effective_at) ? ` (${fmtTime(m.effective_at)})` : ''}
                    </p>
                  ) : null
                })}
                <p className="small">{c.explanation}</p>
              </div>
            ) : (
              <div key={i} className="conflict" role="alert">
                <div className="conflict-title">Memory conflict</div>
                <p>Two team conventions appear relevant.</p>
                {c.memory_ids.map((id, k) => {
                  const m = data.memories.find((x) => x.id === id)
                  return m ? <p key={id} className="conflict-rule"><strong>Rule {k ? 'B' : 'A'}</strong> “{m.text}”</p> : null
                })}
                <p className="small">{c.explanation}</p>
                <button type="button" className="btn memory sm" onClick={() => onTeachException(c, data)}>Teach Exception</button>
              </div>
            )
          ))}

          {data.clean && (
            <div className="clean">
              <span className="check">✓</span>
              <div>
                <strong>No significant issues found.</strong>
                {memory && data.conventions_checked.length > 0 && (
                  <>
                    <p className="small muted">Relevant team conventions this change was checked against:</p>
                    <ul className="conv">
                      {data.conventions_checked.map((id) => {
                        const m = data.memories.find((x) => x.id === id)
                        return m ? <li key={id}>“{m.text}”</li> : null
                      })}
                    </ul>
                  </>
                )}
              </div>
            </div>
          )}

          <div className="issues">
            {data.issues.map((issue, i) => (
              <IssueCard key={`${data.review_id}-${issue.id}`} index={i} issue={issue} memories={data.memories}
                state={decisions[`${data.review_id}:${issue.id}`]}
                onWhy={(is) => onWhy(is, data)} onAccept={(is) => onAccept(is, data)} onReject={(is) => onReject(is, data)}
                onTeach={(is, note) => onTeach(is, data, note)} />
            ))}
          </div>

          {memory && (
            <div className="feedback">
              {fbSent ? (
                <p className="small">{fbSent === 'helpful' ? '👍 Thanks — marked as correct.' : '👎 Feedback recorded.'}</p>
              ) : (
                <>
                  <div className="row gap-s wrap">
                    <span className="small">Was this review useful?</span>
                    <button type="button" className="btn ghost sm" disabled={busy} onClick={() => sendFeedback('helpful')}>👍 Correct</button>
                    <button type="button" className="btn ghost sm" onClick={() => setFbOpen(true)} aria-expanded={fbOpen}>👎 Incorrect</button>
                  </div>
                  {fbOpen && (
                    <div className="teach-inline">
                      <label htmlFor="fb-note" className="small muted">Tell RepoMind what was wrong</label>
                      <textarea id="fb-note" rows={2} value={fbComment} onChange={(e) => setFbComment(e.target.value)} maxLength={1000}
                        placeholder="e.g. Analytics endpoints are allowed to read from replicas directly." />
                      <div className="row gap-s">
                        <button type="button" className="btn memory sm" disabled={busy || fbComment.trim().length < 8} onClick={() => sendFeedback('incorrect', true)}>Teach RepoMind</button>
                        <button type="button" className="btn ghost sm" disabled={busy} onClick={() => sendFeedback('incorrect', false)}>Send without teaching</button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
