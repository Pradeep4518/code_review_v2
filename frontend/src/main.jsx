import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './styles.css'

class Boundary extends React.Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="fatal" role="alert">
        <h1>Something broke in the interface</h1>
        <p>Your data is safe. Reload the page to continue.</p>
        <button className="btn primary" onClick={() => window.location.reload()}>Reload</button>
      </div>
    )
  }
}

createRoot(document.getElementById('root')).render(<Boundary><App /></Boundary>)
