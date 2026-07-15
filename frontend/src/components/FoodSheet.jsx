import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { enqueue } from '../outbox.js'
import { localToday } from '../dates.js'

const fmt = (v, digits = 0) => (v == null ? '—' : Number(v).toFixed(digits))

/* Bottom sheet for one food: portion-first amount entry with raw grams one
   tap behind (PLAN v3.2), live macro+sodium preview, one primary action.
   Logs via POST /api/log with a client-generated client_id; if the network
   is down the entry goes to the outbox and the UI says so honestly. */
export default function FoodSheet({ food, onClose, onLogged, defaultAmount }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  // amount state: mode 'portion' (portionId + qty) or 'grams'
  const [mode, setMode] = useState('portion')
  const [portionId, setPortionId] = useState(null)
  const [qty, setQty] = useState(1)
  const [grams, setGrams] = useState(100)

  useEffect(() => {
    let alive = true
    api(`/api/foods/${food.food_id ?? food.id}`)
      .then((d) => {
        if (!alive) return
        setDetail(d)
        const portions = d.portions ?? []
        if (defaultAmount?.portion_id && portions.some((p) => p.id === defaultAmount.portion_id)) {
          setMode('portion')
          setPortionId(defaultAmount.portion_id)
          setQty(defaultAmount.portion_qty ?? 1)
        } else if (defaultAmount?.grams) {
          setMode('grams')
          setGrams(defaultAmount.grams)
        } else if (portions.length) {
          setMode('portion')
          setPortionId(portions[0].id)
        } else {
          setMode('grams')
        }
      })
      .catch((e) => alive && setError(e.message))
    return () => { alive = false }
  }, [food, defaultAmount])

  const effectiveGrams = useMemo(() => {
    if (!detail) return 0
    if (mode === 'grams') return Number(grams) || 0
    const p = (detail.portions ?? []).find((x) => x.id === portionId)
    return p ? p.gram_weight * qty : 0
  }, [detail, mode, portionId, qty, grams])

  const per = (key) => {
    const v = detail?.nutrients?.[key]
    return v == null ? null : (v * effectiveGrams) / 100
  }

  const log = async () => {
    setBusy(true)
    setError(null)
    const body = {
      client_id: crypto.randomUUID(),
      date: localToday(),
      food_id: detail.food.id,
      entry_method: 'manual',
      ...(mode === 'grams'
        ? { grams: Number(grams) }
        : { portion_id: portionId, portion_qty: qty }),
    }
    try {
      await api('/api/log', { method: 'POST', body })
      onLogged({ queued: false })
    } catch (err) {
      if (err.status === 0) {
        // offline: grams must be resolved client-side since the server can't
        if (mode === 'portion') {
          const p = detail.portions.find((x) => x.id === portionId)
          body.grams = Math.round(p.gram_weight * qty * 100) / 100
          delete body.portion_id
          delete body.portion_qty
        }
        await enqueue(body)
        onLogged({ queued: true })
      } else {
        setError(err.message)
        setBusy(false)
      }
    }
  }

  const [savedFav, setSavedFav] = useState(false)
  const saveFavorite = async () => {
    try {
      await api('/api/favorites', { method: 'POST', body: {
        food_id: detail.food.id,
        ...(mode === 'grams'
          ? { default_grams: Number(grams) }
          : { portion_id: portionId, portion_qty: qty }),
      } })
      setSavedFav(true)
    } catch (err) { setError(err.message) }
  }

  const portions = detail?.portions ?? []

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="row spread">
          <div>
            <b>{food.name}</b>
            {food.brand && <div className="faint">{food.brand}</div>}
          </div>
          <button className="btn secondary small" onClick={onClose}>Close</button>
        </div>

        {error && <div className="error">{error}</div>}
        {!detail && !error && <p className="muted">Loading…</p>}

        {detail && (
          <>
            {portions.length > 0 && (
              <div className="seg" style={{ margin: '12px 0' }}>
                <button className={mode === 'portion' ? 'active' : ''} onClick={() => setMode('portion')}>Portions</button>
                <button className={mode === 'grams' ? 'active' : ''} onClick={() => setMode('grams')}>Grams</button>
              </div>
            )}

            {mode === 'portion' && portions.length > 0 && (
              <>
                <div className="portion-grid">
                  {portions.map((p) => (
                    <button
                      key={p.id}
                      className={`portion-option ${p.id === portionId ? 'selected' : ''}`}
                      onClick={() => setPortionId(p.id)}
                    >
                      <span>{p.description}</span>
                      <span className="faint num">{fmt(p.gram_weight)} g</span>
                    </button>
                  ))}
                </div>
                <div className="qty-row">
                  <button className="qty-btn" onClick={() => setQty((q) => Math.max(0.25, Math.round((q - 0.25) * 100) / 100))}>−</button>
                  <div className="num" style={{ minWidth: 70, textAlign: 'center', fontSize: '1.2rem' }}>× {qty}</div>
                  <button className="qty-btn" onClick={() => setQty((q) => Math.round((q + 0.25) * 100) / 100)}>+</button>
                </div>
              </>
            )}

            {mode === 'grams' && (
              <div className="qty-row">
                <input
                  className="input num" type="number" inputMode="decimal" min="1"
                  value={grams} onChange={(e) => setGrams(e.target.value)}
                />
                <span className="muted">g</span>
              </div>
            )}

            <div className="preview num">
              <span><b>{fmt(effectiveGrams)}</b><span className="faint">g</span></span>
              <span><b>{fmt(per('kcal'))}</b><span className="faint">kcal</span></span>
              <span><b>{fmt(per('protein_g'), 1)}</b><span className="faint">protein</span></span>
              <span><b>{fmt(per('sodium_mg'))}</b><span className="faint">sodium mg</span></span>
            </div>
            {detail.nutrients?.sodium_mg == null && (
              <div className="coverage-note">This food doesn't report sodium — it won't count toward today's sodium total.</div>
            )}

            <button className="btn" style={{ marginTop: 12 }} disabled={busy || effectiveGrams <= 0} onClick={log}>
              {busy ? 'Logging…' : 'Log it'}
            </button>
            <button className="btn secondary" style={{ marginTop: 8 }}
                    disabled={savedFav || effectiveGrams <= 0} onClick={saveFavorite}>
              {savedFav ? '★ Saved to favorites' : '☆ Save this amount as favorite'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
