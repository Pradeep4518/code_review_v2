// Thin client for the RepoMind backend. All calls go through /api (proxied in dev).
async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new Error('Cannot reach the RepoMind backend. Is it running on port 8000?')
  }
  let data = null
  try { data = await res.json() } catch { /* non-JSON body */ }
  if (!res.ok) throw new Error(data?.error?.message || `Request failed (HTTP ${res.status})`)
  return data
}

export const api = {
  health: () => request('/health'),
  review: (body) => request('/review', { method: 'POST', body }),
  compare: (body) => request('/compare', { method: 'POST', body }),
  impact: () => request('/impact'),
  playbook: () => request('/playbook'),
  teach: (body) => request('/teach', { method: 'POST', body }),
  memories: (params = {}) => request('/memories?' + new URLSearchParams(params)),
  updateMemory: (id, body) => request(`/memories/${encodeURIComponent(id)}`, { method: 'PATCH', body }),
  retireMemory: (id, body) => request(`/memories/${encodeURIComponent(id)}/retire`, { method: 'POST', body }),
  restoreMemory: (id, body) => request(`/memories/${encodeURIComponent(id)}/restore`, { method: 'POST', body }),
  supersedeMemory: (id, body) => request(`/memories/${encodeURIComponent(id)}/supersede`, { method: 'POST', body }),
  deleteMemory: (id, actor = '') => request(`/memories/${encodeURIComponent(id)}?` + new URLSearchParams({ actor }), { method: 'DELETE' }),
  seed: () => request('/seed', { method: 'POST' }),
  history: () => request('/history'),
  reviewDetail: (id) => request(`/history/${id}`),
  feedback: (body) => request('/feedback', { method: 'POST', body }),
  analytics: () => request('/analytics'),
  dna: () => request('/repository-dna'),
  resetDemo: () => request('/reset-demo', { method: 'POST' }),
}
