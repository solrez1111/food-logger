import React, { useCallback, useEffect, useState } from 'react'
import { api, getToken, setToken } from './api.js'
import { pending, startAutoFlush } from './outbox.js'
import LogView from './views/LogView.jsx'
import DayView from './views/DayView.jsx'
import TrendsView from './views/TrendsView.jsx'

function SetupScreen({ onDone }) {
  const [value, setValue] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    setToken(value.trim())
    try {
      await api('/api/me')
      onDone()
    } catch (err) {
      setError(err.status === 401 ? 'Token rejected — check for typos.' : `Could not verify: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="view">
      <h1>Food Log</h1>
      <div className="card">
        <h2>One-time setup</h2>
        <p className="muted">Paste the API token for this app. It is stored only on this device.</p>
        <form onSubmit={submit}>
          <input
            className="input" type="password" placeholder="API token" autoComplete="off"
            value={value} onChange={(e) => setValue(e.target.value)}
          />
          {error && <div className="error">{error}</div>}
          <button className="btn" style={{ marginTop: 10 }} disabled={busy || !value.trim()}>
            {busy ? 'Checking…' : 'Save token'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken())
  const [tab, setTab] = useState('log')
  const [queued, setQueued] = useState(0)
  // bump to tell views their data may have changed (outbox flush, new log)
  const [dataVersion, setDataVersion] = useState(0)

  const refreshQueued = useCallback(() => {
    pending().then((p) => setQueued(p.length)).catch(() => {})
    setDataVersion((v) => v + 1)
  }, [])

  useEffect(() => {
    if (authed) startAutoFlush(refreshQueued)
  }, [authed, refreshQueued])

  if (!authed) return <SetupScreen onDone={() => setAuthed(true)} />

  return (
    <div className="app">
      {tab === 'log' && (
        <LogView queued={queued} onLogged={refreshQueued} onAuthLost={() => setAuthed(false)} />
      )}
      {tab === 'day' && <DayView dataVersion={dataVersion} onChanged={refreshQueued} />}
      {tab === 'trends' && <TrendsView dataVersion={dataVersion} />}

      <nav className="tabbar">
        {[['log', '+', 'Log'], ['day', '☰', 'Today'], ['trends', '↗', 'Trends']].map(([id, glyph, label]) => (
          <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
            <span className="glyph">{glyph}</span>
            {label}
          </button>
        ))}
      </nav>
    </div>
  )
}
