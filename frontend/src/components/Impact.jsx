import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { downloadPlaybook } from '../util.js'

const SEV = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info' }
const Stat = ({ value, label }) => <div className="imp-stat"><div className="big-num">{value ?? '—'}</div><div className="small muted">{label}</div></div>

function Pain({ n, problem, fix, children }) {
  return (
    <section className="card pain">
      <header>
        <span className="pain-n" aria-hidden="true">{n}</span>
        <div><h3>{problem}</h3><p className="small muted">{fix}</p></div>
      </header>
      {children}
    </section>
  )
}

export default function Impact({ refreshKey, onError, onToast }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { let live = true; api.impact().then((x) => live && setD(x)).catch((e) => live && setErr(e.message)); return () => { live = false } }, [refreshKey])

  const exportPlaybook = async () => {
    setBusy(true)
    try { const n = await downloadPlaybook(api); onToast(`✓ Playbook exported (${n} rule${n === 1 ? '' : 's'})`) } catch (e) { onError(e.message) } finally { setBusy(false) }
  }

  const empty = d && d.speed.reviews === 0
  const s = d?.speed, r = d?.repeat, c = d?.consistency, g = d?.generic, k = d?.knowledge
  return (
    <div className="page">
      <div className="page-head"><div><h2>Team Impact</h2><p className="muted">Five everyday code-review problems, and what RepoMind does about each one. Numbers come from real reviews in this workspace.</p></div></div>
      {err && <p className="notice bad" role="alert">{err}</p>}
      {!d && !err && <div className="skeleton"><div className="sk w90" /></div>}
      {empty && <p className="notice warn">No reviews yet. Run a few reviews from the Review screen and these cards will fill in.</p>}
      {d && (
        <div className="pain-grid">
          <Pain n="1" problem="Code review is slow" fix="Every PR gets an instant verdict, so developers don’t wait hours for a senior to say “fix this first”.">
            <div className="imp-stats">
              <Stat value={s.avg_review_seconds != null ? `${s.avg_review_seconds}s` : null} label="average review time" />
              <Stat value={s.distinct_prs} label="PRs reviewed instantly" />
              <Stat value={s.blocked_prs} label="blocked before a human looked" />
              <Stat value={s.est_minutes_saved} label="est. senior minutes saved" />
            </div>
            <p className="small muted">{d.assumptions.note}</p>
          </Pain>

          <Pain n="2" problem="The same mistakes repeat" fix="RepoMind notices when one kind of mistake shows up in different PRs, and suggests turning it into a rule.">
            {r.recurring.length === 0 ? <p className="small muted">No mistake has appeared in two different PRs yet.</p> : (
              <ul className="rec-list">
                {r.recurring.map((x) => (
                  <li key={x.title}>
                    <span className={`sev ${x.severity}`}>{SEV[x.severity] || x.severity}</span>
                    <span className="grow">{x.title}</span>
                    <span className="small muted">in {x.prs} PRs</span>
                    <span className={`tag ${x.covered_by_rule ? 'team' : 'warn'}`}>{x.covered_by_rule ? 'Rule exists' : 'No rule yet'}</span>
                  </li>
                ))}
              </ul>
            )}
            {r.not_yet_a_rule > 0 && <p className="small">{r.not_yet_a_rule} repeated mistake{r.not_yet_a_rule > 1 ? 's have' : ' has'} no team rule yet. Use “Teach as Rule” on the issue.</p>}
          </Pain>

          <Pain n="3" problem="Knowledge leaves with people" fix="Each rule keeps who taught it and why it exists, and the whole set exports as a playbook the team owns.">
            <div className="imp-stats">
              <Stat value={k.rules} label="team rules" />
              <Stat value={k.rules ? `${k.with_reason}/${k.rules}` : null} label="have a recorded reason" />
              <Stat value={k.contributors.length} label="contributors" />
            </div>
            {k.contributors.length > 0 && <p className="small muted">Taught by: {k.contributors.join(', ')}</p>}
            <button type="button" className="btn memory sm" onClick={exportPlaybook} disabled={busy || k.rules === 0}>{busy ? 'Exporting…' : 'Export team playbook'}</button>
          </Pain>

          <Pain n="4" problem="Reviews are inconsistent" fix="Every review is checked against the same recalled rules, and re-reviewing the same code shows whether the result changed.">
            <div className="imp-stats">
              <Stat value={c.avg_compliance != null ? `${c.avg_compliance}%` : null} label="average rule compliance" />
              <Stat value={c.repeat_reviews ? `${c.repeat_consistent}/${c.repeat_reviews}` : null} label="repeat reviews that matched" />
            </div>
            <p className="small muted">Compliance = recalled team rules that the code did not break.</p>
          </Pain>

          <Pain n="5" problem="AI tools are generic" fix="A generic AI knows programming, not your company. RepoMind recalls your rules on every review.">
            <div className="imp-stats">
              <Stat value={g.team_specific_findings} label="findings from your team’s rules" />
              <Stat value={g.general_findings} label="general best-practice findings" />
              <Stat value={g.memory_reviews} label="memory-backed PRs" />
            </div>
            <p className="small muted">Use “Compare Reviews” on the Review screen to see what memory adds to a specific PR.</p>
          </Pain>
        </div>
      )}
    </div>
  )
}
