// UI smoke test: renders the real <App/> in jsdom against a LIVE backend (uvicorn on :8000).
// Start the backend first (see README), then: npm test
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { beforeAll, describe, expect, it } from 'vitest'
import App from './App.jsx'

const API = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'
const realFetch = globalThis.fetch
const opts = { timeout: 15000 }

beforeAll(async () => {
  globalThis.fetch = (url, init) => realFetch(typeof url === 'string' && url.startsWith('/') ? API + url : url, init)
  await realFetch(`${API}/api/reset-demo`, { method: 'POST' })
  window.HTMLElement.prototype.scrollIntoView = () => {}
})

const click = (name) => fireEvent.click(screen.getByRole('button', { name }))

describe('RepoMind demo flow', () => {
  it('runs: seed → stateless → Hindsight → teach → re-review → why → other screens', async () => {
    render(<App />)
    await screen.findByText('DEMO MEMORY MODE', {}, opts)

    click('Seed team knowledge')
    await waitFor(() => expect(screen.getByText('Memories').nextSibling.textContent).toBe('9'), opts)

    click('Review Without Memory')
    await screen.findByText('SQL Injection Risk', {}, opts)
    expect(screen.getByText('NO MEMORY')).toBeTruthy()
    expect(screen.queryByText(/Team convention violated/)).toBeNull()

    click('Review With Hindsight')
    await screen.findByText(/database access bypasses the repository layer/, {}, opts)
    expect(screen.getAllByText('Memory used').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/repository\/db\.py/).length).toBeGreaterThan(0)

    fireEvent.change(screen.getByLabelText('Rule'), { target: { value: 'All FastAPI route handlers must remain thin. Database access must never happen directly inside API routes.' } })
    click('Teach Hindsight')
    await screen.findByText('✓ Memory retained', {}, opts)

    click('Review With Hindsight')
    await screen.findByText(/route handler is not thin/, {}, opts)

    const card = screen.getByText(/route handler is not thin/).closest('article')
    fireEvent.click(within(card).getByRole('button', { name: 'Why?' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Why this was flagged')).toBeTruthy()
    expect(within(dialog).getAllByText(/must remain thin/).length).toBeGreaterThan(0)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }))

    fireEvent.click(within(card).getByRole('button', { name: '✓ Accept' }))
    await within(card).findByText('✓ Accepted', {}, opts)

    for (const [nav, expected] of [['Memory Bank', 'Team Engineering Memory'], ['Repository DNA', /What RepoMind currently understands|Repository DNA/], ['Review History', 'Review History'], ['Analytics', 'Memory Impact'], ['Settings', 'Settings']]) {
      fireEvent.click(screen.getByRole('button', { name: nav }))
      await screen.findByRole('heading', { name: expected }, opts)
    }
    fireEvent.click(screen.getByRole('button', { name: 'Memory Bank' }))
    fireEvent.click(await screen.findByRole('tab', { name: 'Timeline' }, opts))
    await screen.findAllByText('Today', {}, opts)
  }, 90000)

  it('shows what memory adds, team impact, and keeps who/why for a rule', async () => {
    await realFetch(`${API}/api/reset-demo`, { method: 'POST' })
    render(<App />)
    await screen.findByText('DEMO MEMORY MODE', {}, opts)
    click('Seed team knowledge')
    await waitFor(() => expect(screen.getByText('Memories').nextSibling.textContent).toBe('9'), opts)

    // teach a rule with an owner and a reason (feature 3: knowledge stays after people leave)
    fireEvent.change(screen.getByLabelText('Rule'), { target: { value: 'Never call the payments API without an idempotency key.' } })
    fireEvent.change(screen.getByLabelText(/Why does this rule exist/), { target: { value: 'A retry once double-charged customers.' } })
    fireEvent.change(screen.getByLabelText('Taught by'), { target: { value: 'Priya' } })
    click('Teach Hindsight')
    await screen.findByText('✓ Memory retained', {}, opts)

    // feature 1/4/5: verdict, checklist and "what memory added" from one Compare request
    click('Compare Reviews')
    await screen.findByText('What memory added over a generic AI', {}, opts)
    expect(screen.getAllByText('Blocked').length).toBe(2)
    expect(screen.getAllByText(/min of senior review time saved/).length).toBe(2)
    expect(screen.getByText('Team standards checklist')).toBeTruthy()

    // feature 1-5 dashboard
    click('Team Impact')
    for (const h of ['Code review is slow', 'The same mistakes repeat', 'Knowledge leaves with people', 'Reviews are inconsistent', 'AI tools are generic']) {
      await screen.findByRole('heading', { name: h }, opts)
    }
    expect(screen.getByRole('button', { name: 'Export team playbook' })).toBeTruthy()

    // feature 3: the memory bank remembers who taught it and why
    click('Memory Bank')
    await screen.findAllByText(/Taught by Priya/, {}, opts)
    expect(screen.getAllByText(/double-charged customers/).length).toBeGreaterThan(0)
  }, 90000)

  it('does not crash when the API is unreachable', async () => {
    globalThis.fetch = () => Promise.reject(new TypeError('offline'))
    render(<App />)
    click('Review With Hindsight')
    await screen.findByText(/Cannot reach the RepoMind backend/, {}, opts)
    expect(screen.getByRole('button', { name: 'Review With Hindsight' })).toBeTruthy()
  })
})
