const SEV = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' }

// Feature: "AI tools are generic". Shows exactly what team memory added on top of a generic AI review.
export default function CompareDelta({ delta }) {
  const t = delta.team_only.length + delta.memory_extra_general.length
  const nothing = t === 0 && delta.upgraded === 0
  return (
    <section className="card delta" aria-label="What memory added">
      <h3>What memory added over a generic AI</h3>
      <div className="delta-stats">
        <div><div className="big-num">{t}</div><div className="small muted">new findings a generic AI missed</div></div>
        <div><div className="big-num">{delta.upgraded}</div><div className="small muted">generic findings now tied to a team rule</div></div>
        <div><div className="big-num">{delta.shared}</div><div className="small muted">found by both</div></div>
      </div>
      {t > 0 && (
        <ul className="delta-list">
          {[...delta.team_only, ...delta.memory_extra_general].map((x, i) => (
            <li key={i}><span className={`sev ${x.severity}`}>{SEV[x.severity] || x.severity}</span> {x.title}</li>
          ))}
        </ul>
      )}
      {delta.generic_only.length > 0 && <p className="small muted">Only the generic review raised: {delta.generic_only.map((x) => x.title).join('; ')}.</p>}
      {nothing && <p className="small muted">Memory did not change this review. Teach more rules, or seed team knowledge, and compare again.</p>}
    </section>
  )
}
