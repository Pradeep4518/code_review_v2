import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { CATEGORIES, dayLabel, downloadPlaybook, fmtTime, providerName } from '../util.js'

const FILTERS = ['All', ...CATEGORIES.filter((c) => c !== 'Other'), 'Other']
const STATUS_LABEL = { active: 'Active', retired: 'Retired', superseded: 'Replaced' }
const ACTION_LABEL = { edited: 'Edited', retired: 'Retired', restored: 'Restored', superseded: 'Replaced', replaces: 'Replaces an older rule' }

function loadActor() { try { return localStorage.getItem('repomind.actor') || '' } catch { return '' } }
function saveActor(v) { try { localStorage.setItem('repomind.actor', v) } catch { /* private mode */ } }

function MemoryRow({ m, onOpen, selected }) {
  const inactive = m.status !== 'active'
  return (
    <li>
      <button type="button" className={`mem-row ${inactive ? 'inactive' : ''} ${selected ? 'selected' : ''}`} onClick={() => onOpen(m)}>
        <span className="row gap wrap">
          <span className={`cat cat-${m.category.toLowerCase()}`}>{m.category}</span>
          {inactive && <span className={`status-badge ${m.status}`}>{STATUS_LABEL[m.status]}</span>}
          {m.edited && <span className="status-badge edited">Edited</span>}
        </span>
        <span className="mem-row-text">{m.text}</span>
        {m.reason && <span className="mem-row-why small">Why: {m.reason}</span>}
        <span className="mem-row-meta small muted">
          <span>Taught by {m.given_by || 'unknown'}</span>
          <span>{fmtTime(m.given_at) ? `Taught ${fmtTime(m.given_at)}` : 'Date not recorded'}</span>
          {m.updated_by && <span>Edited by {m.updated_by}{fmtTime(m.updated_at) ? `, ${fmtTime(m.updated_at)}` : ''}</span>}
          {m.times_applied > 0 && <span>Used in {m.times_applied} review{m.times_applied > 1 ? 's' : ''}</span>}
        </span>
      </button>
    </li>
  )
}

function RuleForm({ form, setForm, ruleLabel, reasonLabel, showCategory = true }) {
  return (
    <div className="form-stack">
      <label htmlFor="rf-rule">{ruleLabel}</label>
      <textarea id="rf-rule" rows={4} maxLength={1000} value={form.rule} onChange={(e) => setForm({ ...form, rule: e.target.value })} />
      {showCategory && (
        <>
          <label htmlFor="rf-cat">Category</label>
          <select id="rf-cat" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
            {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
          </select>
        </>
      )}
      <label htmlFor="rf-why">{reasonLabel}</label>
      <input id="rf-why" maxLength={500} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
    </div>
  )
}

function Detail({ m, actor, onClose, onChanged, onError }) {
  const [mode, setMode] = useState('view') // view | edit | replace | retire | delete
  const [busy, setBusy] = useState(false)
  const [form, setForm] = useState({ rule: '', category: 'Other', reason: '', note: '' })
  useEffect(() => { setMode('view') }, [m.id])
  const active = m.status === 'active'

  const begin = (next) => {
    setForm({ rule: m.text, category: m.category, reason: next === 'replace' ? '' : (m.reason || ''), note: '' })
    setMode(next)
  }
  const run = async (call) => {
    setBusy(true)
    try { const res = await call(); setMode('view'); setBusy(false); await onChanged(res) } catch (e) { onError(e.message) } finally { setBusy(false) }
  }
  const ruleOk = form.rule.trim().length >= 8

  return (
    <aside className="drawer" role="dialog" aria-label="Memory details">
      <header className="modal-head">
        <h3>Rule</h3>
        <button className="btn ghost sm" onClick={onClose} aria-label="Close details">✕</button>
      </header>

      {mode === 'view' && (
        <>
          <p className="detail-text">“{m.text}”</p>
          {!active && (
            <p className={`notice ${m.status === 'superseded' ? 'warn' : 'bad'}`}>
              {m.status === 'superseded' ? 'Replaced' : 'Retired'} by {m.retired_by || 'unknown'}{fmtTime(m.retired_at) ? ` on ${fmtTime(m.retired_at)}` : ''}.
              {m.superseded_by_text ? <> Now: “{m.superseded_by_text}”</> : null}
              {m.retire_reason && m.status === 'retired' ? <> Reason: {m.retire_reason}.</> : null}
              {' '}Reviews no longer use this rule.
            </p>
          )}
          {m.supersedes_text && <p className="notice ok">This rule replaced: “{m.supersedes_text}”</p>}
          <dl className="kv">
            <dt>Status</dt><dd>{STATUS_LABEL[m.status]}</dd>
            <dt>Taught by</dt><dd>{m.given_by || 'Not recorded'}</dd>
            <dt>Taught on</dt><dd>{fmtTime(m.given_at) || 'Not available'}</dd>
            {m.updated_by && (<><dt>Last edited</dt><dd>{m.updated_by}{fmtTime(m.updated_at) ? `, ${fmtTime(m.updated_at)}` : ''}</dd></>)}
            <dt>Category</dt><dd>{m.category}</dd>
            <dt>Why it exists</dt><dd>{m.reason || 'Not recorded yet'}</dd>
            <dt>Source</dt><dd>{m.source || 'Not recorded'}</dd>
            <dt>Last used</dt><dd>{fmtTime(m.last_used) || 'Not applied yet'}</dd>
            <dt>Times applied</dt><dd>{m.times_applied}</dd>
            {m.fact_type && (<><dt>Hindsight type</dt><dd>{m.fact_type}</dd></>)}
          </dl>

          <div className="drawer-actions" role="group" aria-label="Rule actions">
            {active ? (
              <>
                <button className="btn sm" onClick={() => begin('edit')}>Edit</button>
                <button className="btn sm memory" onClick={() => begin('replace')}>Replace with newer rule</button>
                <button className="btn sm" onClick={() => begin('retire')}>Retire</button>
              </>
            ) : (
              <button className="btn sm ok" disabled={busy} onClick={() => run(() => api.restoreMemory(m.id, { actor }))}>Restore rule</button>
            )}
            <button className="btn sm bad" onClick={() => setMode('delete')}>Delete…</button>
          </div>
          {!actor.trim() && <p className="small muted">Tip: enter your name at the top so changes are attributed to you.</p>}

          <h4>History</h4>
          <ul className="history">
            <li><span className="small muted">{fmtTime(m.given_at) || '—'}</span> Taught by <strong>{m.given_by || 'unknown'}</strong></li>
            {m.history.map((h, i) => (
              <li key={i}>
                <span className="small muted">{fmtTime(h.at)}</span> {ACTION_LABEL[h.action] || h.action} by <strong>{h.by}</strong>
                {h.detail && <div className="small muted">{h.detail}</div>}
              </li>
            ))}
          </ul>

          <h4>Related reviews</h4>
          {m.related_reviews?.length ? (
            <ul className="plain">{m.related_reviews.map((r) => <li key={r.review_id}>{r.pr_title}</li>)}</ul>
          ) : <p className="muted small">No review has applied this rule yet.</p>}
        </>
      )}

      {mode === 'edit' && (
        <>
          <p className="small muted">Fix wording, category or the reason. The rule keeps its history, and reviews use the new wording straight away.</p>
          <RuleForm form={form} setForm={setForm} ruleLabel="Rule" reasonLabel="Why does this rule exist?" />
          <div className="row gap">
            <button className="btn memory sm" disabled={busy || !ruleOk} onClick={() => run(() => api.updateMemory(m.id, { rule: form.rule, category: form.category, reason: form.reason, actor }))}>{busy ? 'Saving…' : 'Save changes'}</button>
            <button className="btn ghost sm" disabled={busy} onClick={() => setMode('view')}>Cancel</button>
          </div>
        </>
      )}

      {mode === 'replace' && (
        <>
          <p className="small muted">Write the newer rule. The old one is kept in history as “Replaced”, and reviews use only the new rule.</p>
          <p className="notice warn small">Replacing: “{m.text}”</p>
          <RuleForm form={form} setForm={setForm} ruleLabel="New rule" reasonLabel="Why did the standard change?" />
          <div className="row gap">
            <button className="btn memory sm" disabled={busy || !ruleOk || form.rule.trim() === m.text} onClick={() => run(() => api.supersedeMemory(m.id, { rule: form.rule, category: form.category, reason: form.reason, actor }))}>{busy ? 'Replacing…' : 'Replace rule'}</button>
            <button className="btn ghost sm" disabled={busy} onClick={() => setMode('view')}>Cancel</button>
          </div>
        </>
      )}

      {mode === 'retire' && (
        <>
          <p className="detail-text">Retire this rule?</p>
          <p className="small muted">Reviews stop using it, but it stays in the list under “Retired & replaced” and can be restored.</p>
          <label htmlFor="rf-note">Why is it outdated? <span className="muted">(optional)</span></label>
          <input id="rf-note" maxLength={500} value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          <div className="row gap">
            <button className="btn sm" disabled={busy} onClick={() => run(() => api.retireMemory(m.id, { actor, reason: form.note }))}>{busy ? 'Retiring…' : 'Retire rule'}</button>
            <button className="btn ghost sm" disabled={busy} onClick={() => setMode('view')}>Cancel</button>
          </div>
        </>
      )}

      {mode === 'delete' && (
        <>
          <p className="detail-text">Delete this rule permanently?</p>
          <p className="notice bad small">“{m.text}” and its history will be removed. If the standard just changed, use Retire or Replace instead so the history is kept.</p>
          <div className="row gap">
            <button className="btn bad sm" disabled={busy} onClick={() => run(async () => { const r = await api.deleteMemory(m.id, actor); return { ...r, deleted: true } })}>{busy ? 'Deleting…' : 'Delete permanently'}</button>
            <button className="btn ghost sm" disabled={busy} onClick={() => setMode('view')}>Keep it</button>
          </div>
        </>
      )}
    </aside>
  )
}

export default function MemoryBank({ refreshKey, onError, onSeeded, onChanged }) {
  const [data, setData] = useState(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('All')
  const [tab, setTab] = useState('active') // active | inactive | all
  const [view, setView] = useState('list')
  const [open, setOpen] = useState(null)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState('')
  const [actor, setActor] = useState(loadActor)

  const load = useCallback(async () => {
    try {
      const d = await api.memories({ q, category: filter, status: tab })
      setData(d)
      setOpen((cur) => (cur ? d.memories.find((x) => x.id === cur.id) || cur : cur)) // keep the drawer in sync with fresh data
    } catch (e) { onError(e.message) } finally { setLoading(false) }
  }, [q, filter, tab, onError])
  useEffect(() => { const t = setTimeout(load, q ? 200 : 0); return () => clearTimeout(t) }, [load, refreshKey, q])

  const changed = async (res) => {
    if (res.deleted) setOpen(null); else if (res.memory) setOpen(res.memory)
    setToast([res.message, res.warning].filter(Boolean).join(' '))
    onChanged?.() // refresh the counters elsewhere (sidebar, health)
    await load()
  }

  const groups = []
  if (data) {
    for (const m of data.memories) {
      const label = dayLabel(m.given_at)
      let g = groups.find((x) => x.label === label)
      if (!g) { g = { label, items: [] }; groups.push(g) }
      g.items.push(m)
    }
  }
  const inactiveN = data?.inactive_total ?? 0

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Team Engineering Memory</h2>
          <p className="muted">{data ? `${data.total} ${data.total === 1 ? 'memory' : 'memories'} · ${providerName(data.provider)}` : 'Loading…'}</p>
        </div>
        <div className="actor">
          <label htmlFor="actor" className="small muted">Your name</label>
          <input id="actor" value={actor} maxLength={80} placeholder="Who is making changes?" onChange={(e) => { setActor(e.target.value); saveActor(e.target.value) }} />
        </div>
        <button type="button" className="btn ghost sm" disabled={!data || data.total === 0} onClick={async () => { try { await downloadPlaybook(api) } catch (e) { onError(e.message) } }}>Export playbook</button>
        <div className="seg" role="tablist" aria-label="View">
          <button role="tab" aria-selected={view === 'list'} className={view === 'list' ? 'on' : ''} onClick={() => setView('list')}>Memory Bank</button>
          <button role="tab" aria-selected={view === 'timeline'} className={view === 'timeline' ? 'on' : ''} onClick={() => setView('timeline')}>Timeline</button>
        </div>
      </div>
      {data?.warning && <p className="notice warn">{data.warning}</p>}
      {toast && <p className="notice ok" role="status">{toast} <button type="button" className="linklike" onClick={() => setToast('')}>Dismiss</button></p>}
      <div className="toolbar">
        <label className="sr" htmlFor="mq">Search memories</label>
        <input id="mq" type="search" placeholder="Search memories..." value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="filters" role="group" aria-label="Rule status">
          {[['active', `Active${data ? ` (${data.total})` : ''}`], ['inactive', `Retired & replaced${data ? ` (${inactiveN})` : ''}`], ['all', 'All']].map(([k, label]) => (
            <button key={k} type="button" className={`pill ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)} aria-pressed={tab === k}>{label}</button>
          ))}
        </div>
        <div className="filters" role="group" aria-label="Filter by category">
          {FILTERS.map((f) => (
            <button key={f} type="button" className={`pill ${filter === f ? 'on' : ''}`} onClick={() => setFilter(f)} aria-pressed={filter === f}>
              {f}{data && f !== 'All' ? <span className="count">{data.counts[f] ?? 0}</span> : null}
            </button>
          ))}
        </div>
      </div>

      <div className={`mem-layout ${open ? 'with-detail' : ''}`}>
        <div>
          {loading && <div className="skeleton"><div className="sk w90" /><div className="sk w75" /></div>}
          {!loading && data && data.total === 0 && inactiveN === 0 && (
            <div className="empty big">
              <p>No team knowledge yet. Teach Hindsight a rule from the Review screen, or load a starter set.</p>
              <button className="btn memory" onClick={async () => { try { await onSeeded(); await load() } catch (e) { onError(e.message) } }}>Seed team knowledge</button>
            </div>
          )}
          {!loading && data && (data.total > 0 || inactiveN > 0) && data.memories.length === 0 && (
            <div className="empty"><p>{tab === 'inactive' ? 'No retired or replaced rules.' : 'No memories match this filter.'}</p></div>
          )}
          {view === 'list' && <ul className="mem-list">{data?.memories.map((m) => <MemoryRow key={m.id} m={m} onOpen={setOpen} selected={open?.id === m.id} />)}</ul>}
          {view === 'timeline' && groups.map((g) => (
            <section key={g.label} className="tl-group">
              <h4 className="tl-day">{g.label}</h4>
              <ul className="mem-list tl">{g.items.map((m) => <MemoryRow key={m.id} m={m} onOpen={setOpen} selected={open?.id === m.id} />)}</ul>
            </section>
          ))}
        </div>
        {open && <Detail m={open} actor={actor} onClose={() => setOpen(null)} onChanged={changed} onError={onError} />}
      </div>
    </div>
  )
}
