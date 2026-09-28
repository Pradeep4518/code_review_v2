import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { CATEGORIES, dayLabel, downloadPlaybook, fmtTime, providerName } from '../util.js'

const FILTERS = ['All', ...CATEGORIES.filter((c) => c !== 'Other'), 'Other']

function MemoryRow({ m, onOpen }) {
  return (
    <li>
      <button type="button" className="mem-row" onClick={() => onOpen(m)}>
        <span className={`cat cat-${m.category.toLowerCase()}`}>{m.category}</span>
        <span className="mem-row-text">{m.text}</span>
        {m.reason && <span className="mem-row-why small">Why: {m.reason}</span>}
        <span className="mem-row-meta small muted">
          {m.owner && <span>Taught by {m.owner}</span>}
          {m.source && !m.owner && <span>{m.source}</span>}
          {fmtTime(m.created_at) && <span>{fmtTime(m.created_at)}</span>}
          {m.times_applied > 0 && <span>Used in {m.times_applied} review{m.times_applied > 1 ? 's' : ''}</span>}
        </span>
      </button>
    </li>
  )
}

function Detail({ m, onClose }) {
  return (
    <aside className="drawer" role="dialog" aria-label="Memory details">
      <header className="modal-head">
        <h3>Memory</h3>
        <button className="btn ghost sm" onClick={onClose} aria-label="Close details">✕</button>
      </header>
      <p className="detail-text">“{m.text}”</p>
      <dl className="kv">
        <dt>Category</dt><dd>{m.category}</dd>
        <dt>Taught by</dt><dd>{m.owner || 'Not recorded'}</dd>
        <dt>Why it exists</dt><dd>{m.reason || 'Not recorded yet'}</dd>
        <dt>Source</dt><dd>{m.source || 'Not recorded'}</dd>
        <dt>Created</dt><dd>{fmtTime(m.created_at) || 'Not available'}</dd>
        <dt>Last used</dt><dd>{fmtTime(m.last_used) || 'Not applied yet'}</dd>
        <dt>Times applied</dt><dd>{m.times_applied}</dd>
        {m.fact_type && (<><dt>Hindsight type</dt><dd>{m.fact_type}</dd></>)}
      </dl>
      <h4>Related reviews</h4>
      {m.related_reviews?.length ? (
        <ul className="plain">{m.related_reviews.map((r) => <li key={r.review_id}>{r.pr_title}</li>)}</ul>
      ) : <p className="muted small">No review has applied this memory yet.</p>}
    </aside>
  )
}

export default function MemoryBank({ refreshKey, onError, onSeeded }) {
  const [data, setData] = useState(null)
  const [q, setQ] = useState('')
  const [filter, setFilter] = useState('All')
  const [view, setView] = useState('list')
  const [open, setOpen] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try { setData(await api.memories({ q, category: filter })) } catch (e) { onError(e.message) } finally { setLoading(false) }
  }, [q, filter, onError])
  useEffect(() => { const t = setTimeout(load, q ? 200 : 0); return () => clearTimeout(t) }, [load, refreshKey, q])

  const groups = []
  if (data) {
    for (const m of data.memories) {
      const label = dayLabel(m.created_at)
      let g = groups.find((x) => x.label === label)
      if (!g) { g = { label, items: [] }; groups.push(g) }
      g.items.push(m)
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Team Engineering Memory</h2>
          <p className="muted">{data ? `${data.total} ${data.total === 1 ? 'memory' : 'memories'} · ${providerName(data.provider)}` : 'Loading…'}</p>
        </div>
        <button type="button" className="btn ghost sm" disabled={!data || data.total === 0} onClick={async () => { try { await downloadPlaybook(api) } catch (e) { onError(e.message) } }}>Export playbook</button>
        <div className="seg" role="tablist" aria-label="View">
          <button role="tab" aria-selected={view === 'list'} className={view === 'list' ? 'on' : ''} onClick={() => setView('list')}>Memory Bank</button>
          <button role="tab" aria-selected={view === 'timeline'} className={view === 'timeline' ? 'on' : ''} onClick={() => setView('timeline')}>Timeline</button>
        </div>
      </div>
      {data?.warning && <p className="notice warn">{data.warning}</p>}
      <div className="toolbar">
        <label className="sr" htmlFor="mq">Search memories</label>
        <input id="mq" type="search" placeholder="Search memories..." value={q} onChange={(e) => setQ(e.target.value)} />
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
          {!loading && data && data.total === 0 && (
            <div className="empty big">
              <p>No team knowledge yet. Teach Hindsight a rule from the Review screen, or load a starter set.</p>
              <button className="btn memory" onClick={async () => { try { await onSeeded(); await load() } catch (e) { onError(e.message) } }}>Seed team knowledge</button>
            </div>
          )}
          {!loading && data && data.total > 0 && data.memories.length === 0 && <div className="empty"><p>No memories match this filter.</p></div>}
          {view === 'list' && <ul className="mem-list">{data?.memories.map((m) => <MemoryRow key={m.id} m={m} onOpen={setOpen} />)}</ul>}
          {view === 'timeline' && groups.map((g) => (
            <section key={g.label} className="tl-group">
              <h4 className="tl-day">{g.label}</h4>
              <ul className="mem-list tl">{g.items.map((m) => <MemoryRow key={m.id} m={m} onOpen={setOpen} />)}</ul>
            </section>
          ))}
        </div>
        {open && <Detail m={open} onClose={() => setOpen(null)} />}
      </div>
    </div>
  )
}
