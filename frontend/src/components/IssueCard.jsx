import { useState } from 'react'

const SEV = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' }

export default function IssueCard({ issue, memories, state, onWhy, onAccept, onReject, onTeach, index }) {
  const [teaching, setTeaching] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const used = memories.filter((m) => issue.memory_ids.includes(m.id))

  const submitTeach = async (e) => {
    e.preventDefault()
    setBusy(true)
    const ok = await onTeach(issue, note.trim())
    setBusy(false)
    if (ok) setTeaching(false)
  }

  return (
    <article className={`issue sev-${issue.severity}`} style={{ animationDelay: `${Math.min(index, 6) * 50}ms` }}>
      <header className="issue-head">
        <span className={`sev ${issue.severity}`}>{SEV[issue.severity] || issue.severity}</span>
        <h4>{issue.title}</h4>
        {issue.source_type === 'team' ? <span className="tag team">Team convention</span> : <span className="tag">General best practice</span>}
        {issue.conflict && <span className="tag warn">Conflict</span>}
        {issue.seen_before > 0 && <span className="tag repeat" title="Same kind of mistake flagged in earlier, different PRs">Seen in {issue.seen_before} earlier PR{issue.seen_before > 1 ? 's' : ''}</span>}
      </header>
      {issue.description && <p className="issue-desc">{issue.description}</p>}
      {issue.code && (
        <pre className="snippet"><span className="ln">{issue.line ?? ''}</span>{issue.code}</pre>
      )}
      {issue.suggest_rule && !teaching && (
        <p className="repeat-hint">This mistake keeps coming back and no team rule covers it yet. <button type="button" className="linklike" onClick={() => setTeaching(true)}>Turn it into a rule</button></p>
      )}
      {issue.recommendation && (
        <p className="rec"><strong>Recommendation</strong> {issue.recommendation}</p>
      )}
      {used.length > 0 && (
        <div className="memory-used" role="note">
          <div className="memory-used-title">Memory used</div>
          {used.map((m) => (
            <div key={m.id} className="memory-used-item">
              <span className="mem-label">{m.label || `${m.category} rule`}</span>
              <span className="mem-text">“{m.text}”</span>
            </div>
          ))}
        </div>
      )}
      <footer className="issue-actions">
        <button type="button" className="btn ghost sm" onClick={() => onWhy(issue)}>Why?</button>
        {state ? (
          <span className={`decided ${state}`}>{state === 'accepted' ? '✓ Accepted' : state === 'rejected' ? '✕ Rejected' : '✓ Rule taught'}</span>
        ) : (
          <>
            <button type="button" className="btn ok sm" onClick={() => onAccept(issue)}>✓ Accept</button>
            <button type="button" className="btn bad sm" onClick={() => onReject(issue)}>✕ Reject</button>
          </>
        )}
        <button type="button" className="btn memory sm" onClick={() => setTeaching((v) => !v)} aria-expanded={teaching}>Teach as Rule</button>
      </footer>
      {teaching && (
        <form className="teach-inline" onSubmit={submitTeach}>
          <label htmlFor={`note-${issue.id}`} className="small muted">Rule to remember (leave empty to use this recommendation)</label>
          <textarea id={`note-${issue.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000}
            placeholder={issue.recommendation || 'Describe the team rule'} />
          <div className="row gap-s">
            <button className="btn memory sm" disabled={busy}>{busy ? 'Retaining…' : 'Teach Hindsight'}</button>
            <button type="button" className="btn ghost sm" onClick={() => setTeaching(false)}>Cancel</button>
          </div>
        </form>
      )}
    </article>
  )
}
