import { useEffect, useRef } from 'react'
import { providerName } from '../util.js'

export default function WhyModal({ issue, review, onClose }) {
  const ref = useRef(null)
  useEffect(() => {
    const prev = document.activeElement
    ref.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [onClose])

  const mems = review.memories.filter((m) => issue.memory_ids.includes(m.id))
  const team = mems.length > 0
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="why-title" tabIndex={-1} ref={ref}>
        <header className="modal-head">
          <h2 id="why-title">Why this was flagged</h2>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="why-block">
          <div className="why-label">Issue</div>
          <p><strong>{issue.title}</strong></p>
        </div>
        <div className="why-block">
          <div className="why-label">Code</div>
          {issue.code ? <pre className="snippet"><span className="ln">{issue.line ?? ''}</span>{issue.code}</pre> : <p className="muted">This finding is about the change as a whole, not one line.</p>}
        </div>
        <div className="why-block">
          <div className="why-label">Team rule</div>
          {team ? mems.map((m) => (
            <div key={m.id} className="memory-used-item big">
              <span className="mem-label">{m.label || `${m.category} rule`}</span>
              <span className="mem-text">“{m.text}”</span>
            </div>
          )) : <p className="muted">No team memory was applied. This is a general best practice.</p>}
        </div>
        <div className="why-block">
          <div className="why-label">Reason</div>
          <p>{issue.reason || issue.description}</p>
        </div>
        <div className="why-block">
          <div className="why-label">Memory source</div>
          <p>{team ? (review.memory_provider === 'hindsight' ? 'Hindsight' : providerName(review.memory_provider)) : 'None — generic review'}</p>
          {team && mems[0].last_used && <p className="small muted">Applied {mems[0].times_applied}× so far.</p>}
        </div>
      </div>
    </div>
  )
}
