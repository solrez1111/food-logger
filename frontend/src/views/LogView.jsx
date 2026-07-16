import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import { localToday } from '../dates.js'
import FoodSheet from '../components/FoodSheet.jsx'
import Scanner from '../components/Scanner.jsx'

const kcal100 = (f) => f.per_100g?.kcal != null ? `${Math.round(f.per_100g.kcal)} kcal/100g` : ''

/* Log screen, ordered by PLAN decision 10's hierarchy:
   camera (Phase 5) → favorites → recents → search → barcode → Search USDA. */
export default function LogView({ queued, onLogged, onAuthLost }) {
  const [favorites, setFavorites] = useState([])
  const [recent, setRecent] = useState([])
  const [q, setQ] = useState('')
  const [results, setResults] = useState(null)   // null = not searching
  const [searching, setSearching] = useState(false)
  const [picked, setPicked] = useState(null)     // {food, defaultAmount}
  const [scanning, setScanning] = useState(false)
  const [flash, setFlash] = useState(null)       // {text, tone}
  const [error, setError] = useState(null)
  const debounceRef = useRef(null)

  const loadLists = () => {
    api('/api/favorites').then((d) => setFavorites(d.favorites)).catch(bail)
    api('/api/log/recent').then((d) => setRecent(d.recent)).catch(bail)
  }
  const bail = (err) => {
    if (err.status === 401) onAuthLost()
    else setError(err.message)
  }

  useEffect(loadLists, [])

  // search-as-you-type is LOCAL ONLY (decision 3); debounce keeps it calm
  useEffect(() => {
    clearTimeout(debounceRef.current)
    if (q.trim().length < 2) { setResults(null); return }
    debounceRef.current = setTimeout(async () => {
      setSearching(true)
      try {
        setResults(await api(`/api/foods/search?q=${encodeURIComponent(q.trim())}`))
      } catch (err) { bail(err) } finally { setSearching(false) }
    }, 250)
    return () => clearTimeout(debounceRef.current)
  }, [q])

  const searchRemote = async () => {
    setSearching(true)
    try {
      setResults(await api(`/api/foods/search?q=${encodeURIComponent(q.trim())}&remote=1`))
    } catch (err) {
      setError(err.status === 503 ? 'USDA search needs FDC_API_KEY set on the server.' : err.message)
    } finally { setSearching(false) }
  }

  const logFavorite = async (fav) => {
    try {
      await api('/api/log', { method: 'POST', body: {
        client_id: crypto.randomUUID(), date: localToday(), favorite_id: fav.id,
      } })
      showFlash(`Logged ${fav.name}`)
      onLogged()
      loadLists()
    } catch (err) { bail(err) }
  }

  const onScanCode = async (code) => {
    setScanning(false)
    setSearching(true)
    try {
      const detail = await api(`/api/foods/barcode/${encodeURIComponent(code)}`)
      setPicked({ food: { ...detail.food }, defaultAmount: null })
    } catch (err) {
      setError(err.status === 404
        ? `Barcode ${code} not found locally, on Open Food Facts, or USDA.`
        : err.message)
    } finally { setSearching(false) }
  }

  const showFlash = (text) => {
    setFlash(text)
    setTimeout(() => setFlash(null), 2500)
  }

  const sheetDone = ({ queued: wasQueued }) => {
    setPicked(null)
    setQ('')
    setResults(null)
    showFlash(wasQueued ? 'Offline — queued, will sync when back online' : 'Logged ✓')
    onLogged()
    loadLists()
  }

  return (
    <div className="view">
      <h1>Log</h1>
      {queued > 0 && <div className="queued-banner num">{queued} entr{queued === 1 ? 'y' : 'ies'} queued offline — will sync automatically</div>}
      {flash && <div className="card" style={{ borderColor: 'var(--green)', color: 'var(--green)' }}>{flash}</div>}
      {error && <div className="error" onClick={() => setError(null)}>{error} (tap to dismiss)</div>}

      <button className="camera-btn" onClick={() => showFlash('Photo logging arrives in Phase 5')}>
        <span className="glyph">📷</span> Estimate my plate
      </button>

      {favorites.length > 0 && (
        <>
          <h2>Favorites</h2>
          <div className="chips">
            {favorites.map((f) => (
              <button key={f.id} className="chip" onClick={() => logFavorite(f)}>
                {f.name} <span className="kcal num">{Math.round(f.per_serving?.kcal ?? 0)} kcal</span>
              </button>
            ))}
          </div>
        </>
      )}

      <div className="row" style={{ margin: '10px 0' }}>
        <input
          className="input" placeholder="Search foods…" value={q}
          onChange={(e) => setQ(e.target.value)} autoCapitalize="none"
        />
        <button className="btn secondary small" style={{ minHeight: 48 }} onClick={() => setScanning(true)}>
          ▮▮▮
        </button>
      </div>

      {results && (
        <div className="card">
          <h2>{searching ? 'Searching…' : `Results ${
            results.matched === 'trgm' ? '(fuzzy match)'
            : results.matched === 'fts_partial' ? '(no exact match — closest)'
            : ''}`}</h2>
          {results.results.map((r) => (
            <button key={r.id} className="food-row" onClick={() => setPicked({ food: r, defaultAmount: null })}>
              <span>
                {r.name}
                {r.brand && <span className="sub"> — {r.brand}</span>}
              </span>
              <span className="kcal num">{kcal100(r)}</span>
            </button>
          ))}
          {results.results.length === 0 && <p className="muted">Nothing local matches.</p>}
          {results.offer_remote && (
            <button className="btn secondary" style={{ marginTop: 8 }} onClick={searchRemote} disabled={searching}>
              Search USDA →
            </button>
          )}
        </div>
      )}

      {!results && recent.length > 0 && (
        <div className="card">
          <h2>Recent</h2>
          {recent.map((r) => (
            <button
              key={r.food_id} className="food-row"
              onClick={() => setPicked({
                food: r,
                defaultAmount: {
                  grams: r.last_portion_id ? null : r.last_grams,
                  portion_id: r.last_portion_id,
                  portion_qty: r.last_portion_qty,
                },
              })}
            >
              <span>
                {r.name}
                <span className="sub">
                  {' '}last: {r.last_portion_description ? `${r.last_portion_qty} × ${r.last_portion_description}` : `${Math.round(r.last_grams)}g`}
                </span>
              </span>
              <span className="kcal num">{kcal100(r)}</span>
            </button>
          ))}
        </div>
      )}

      {!results && recent.length === 0 && favorites.length === 0 && (
        <div className="card">
          <p className="muted">Nothing logged yet. Search for a food or scan a barcode to make your first entry — it'll show up here as a recent.</p>
        </div>
      )}

      {picked && (
        <FoodSheet
          food={picked.food} defaultAmount={picked.defaultAmount}
          onClose={() => setPicked(null)} onLogged={sheetDone}
        />
      )}
      {scanning && <Scanner onCode={onScanCode} onClose={() => setScanning(false)} />}
    </div>
  )
}
