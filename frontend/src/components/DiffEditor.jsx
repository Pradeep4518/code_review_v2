import { useRef, useState } from 'react'

function lineClass(l) {
  if (l.startsWith('+++') || l.startsWith('---') || l.startsWith('diff ') || l.startsWith('index ')) return 'meta'
  if (l.startsWith('@@')) return 'hunk'
  if (l.startsWith('+')) return 'add'
  if (l.startsWith('-')) return 'del'
  return ''
}

// Textarea over a highlight layer: real editing, diff-aware line tinting, line numbers, focus line.
export default function DiffEditor({ value, onChange, onReset, focusLine }) {
  const layer = useRef(null)
  const gutter = useRef(null)
  const [copied, setCopied] = useState(false)
  const lines = value.split('\n')

  const sync = (e) => {
    const { scrollTop, scrollLeft } = e.target
    if (layer.current) { layer.current.scrollTop = scrollTop; layer.current.scrollLeft = scrollLeft }
    if (gutter.current) gutter.current.scrollTop = scrollTop
  }
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1400) } catch { /* clipboard blocked */ }
  }

  return (
    <div className="editor">
      <div className="editor-bar">
        <span className="muted small">{lines.length} lines</span>
        <div className="row gap-s">
          <button type="button" className="btn ghost xs" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
          <button type="button" className="btn ghost xs" onClick={onReset}>Reset</button>
        </div>
      </div>
      <div className="editor-body">
        <div className="gutter" ref={gutter} aria-hidden="true">
          {lines.map((_, i) => <div key={i} className={focusLine === i + 1 ? 'on' : ''}>{i + 1}</div>)}
        </div>
        <div className="stack-layer">
          <div className="hl" ref={layer} aria-hidden="true">
            {lines.map((l, i) => <div key={i} className={`hl-line ${lineClass(l)} ${focusLine === i + 1 ? 'focus' : ''}`}>{l || ' '}</div>)}
          </div>
          <textarea
            className="code-input" value={value} spellCheck={false} onScroll={sync} wrap="off"
            aria-label="Code diff" onChange={(e) => onChange(e.target.value)}
          />
        </div>
      </div>
    </div>
  )
}
