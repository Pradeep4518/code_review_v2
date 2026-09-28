export const CATEGORIES = ['Architecture', 'Database', 'Security', 'Logging', 'Testing', 'API', 'Performance', 'Other']
export const MODES = [
  ['general', 'General Review'], ['security', 'Security Review'], ['architecture', 'Architecture Review'],
  ['performance', 'Performance Review'], ['full', 'Full Review'],
]
export const modeLabel = (m) => (MODES.find(([k]) => k === m) || [m, m])[1]

export function fmtTime(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function dayLabel(iso) {
  if (!iso) return 'Undated'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Undated'
  const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((start(new Date()) - start(d)) / 86400000)
  if (diff <= 0) return 'Today'
  if (diff === 1) return 'Yesterday'
  return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
}

export const providerName = (p) => (p === 'hindsight' ? 'Hindsight' : p === 'local' ? 'Demo memory (local fallback)' : 'None')

export const VERDICT_LABEL = { blocked: 'Blocked', changes: 'Needs changes', ready: 'Ready' }

// Downloads the team playbook (markdown) so the team's knowledge lives outside any one person's head.
export async function downloadPlaybook(api) {
  const p = await api.playbook()
  const url = URL.createObjectURL(new Blob([p.markdown], { type: 'text/markdown' }))
  const a = document.createElement('a')
  a.href = url; a.download = p.filename; document.body.appendChild(a); a.click(); a.remove()
  URL.revokeObjectURL(url)
  return p.count
}
