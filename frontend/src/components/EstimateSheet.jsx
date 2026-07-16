import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { enqueue } from '../outbox.js'
import { localToday } from '../dates.js'

const fmt = (v, d = 0) => (v == null ? '—' : Number(v).toFixed(d))

/* Phase 5 confirm-before-save sheet. POSTs the photo/text for estimation,
   then shows editable candidates — swap matches, adjust grams, exclude rows,
   manually match the unmatched — with live totals. Nothing is logged until
   "Log N items". */
export default function EstimateSheet({ payload, onClose, onLogged }) {
  const [state, setState] = useState({ phase: 'loading' })
  const [rows, setRows] = useState([])

  useEffect(() => {
    let alive = true
    api('/api/log/estimate', { method: 'POST', body: payload })
      .then((res) => {
        if (!alive) return
        setRows(res.candidates.map((c, i) => ({
          ...c, key: i, included: !!c.food,
          options: c.food ? [c.food, ...c.alternatives] : [],
          search: '', searchResults: null,
        })))
        setState({ phase: 'ready', note: res.note, entryMethod: res.entry_method })
      })
      .catch((err) => alive && setState({
        phase: 'error',
        message: err.status === 503 ? 'AI estimation needs ANTHROPIC_API_KEY set on the server.'
          : err.status === 0 ? 'You appear to be offline — AI estimation needs a connection. Use search or favorites instead.'
          : err.message,
      }))
    return () => { alive = false }
  }, [payload])

  const update = (key, patch) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)))

  const rowSearch = async (row) => {
    if (!row.search.trim()) return
    try {
      const res = await api(`/api/foods/search?q=${encodeURIComponent(row.search.trim())}`)
      update(row.key, { searchResults: res.results.slice(0, 5) })
    } catch (err) {
      update(row.key, { searchResults: [] })
    }
  }

  const perRow = (row, keyName) => {
    const v = row.food?.per_100g?.[keyName]
    return v == null ? null : (v * row.grams) / 100
  }
  const totals = useMemo(() => {
    const sum = (k) => {
      const vals = rows.filter((r) => r.included && r.food).map((r) => perRow(r, k)).filter((v) => v != null)
      return vals.length ? vals.reduce((a, b) => a + b, 0) : null
    }
    return { kcal: sum('kcal'), protein_g: sum('protein_g'), sodium_mg: sum('sodium_mg') }
  }, [rows])

  const toLog = rows.filter((r) => r.included && r.food && r.grams > 0)

  const confirm = async () => {
    setState((s) => ({ ...s, phase: 'saving' }))
    let queued = 0
    for (const row of toLog) {
      const body = {
        client_id: crypto.randomUUID(), date: localToday(),
        food_id: row.food.id, grams: Number(row.grams),
        entry_method: state.entryMethod,
      }
      try {
        await api('/api/log', { method: 'POST', body })
      } catch (err) {
        if (err.status === 0) { await enqueue(body); queued += 1 }
        else { setState({ phase: 'ready', note: `Failed on ${row.food.name}: ${err.message}` }); return }
      }
    }
    onLogged({ count: toLog.length, queued })
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="row spread" style={{ marginBottom: 8 }}>
          <b>{payload.image_b64 ? 'Plate estimate' : 'Meal estimate'}</b>
          <button className="btn secondary small" onClick={onClose}>Cancel</button>
        </div>

        {state.phase === 'loading' && <p className="muted">Analyzing{payload.image_b64 ? ' photo' : ''}… a few seconds.</p>}
        {state.phase === 'error' && <div className="error">{state.message}</div>}
        {state.note && <p className="faint">{state.note}</p>}

        {(state.phase === 'ready' || state.phase === 'saving') && (
          <>
            {rows.length === 0 && <p className="muted">No foods identified. Try a clearer photo or describe the meal.</p>}

            {rows.map((row) => (
              <div key={row.key} className="card" style={{ padding: 10, opacity: row.included || !row.food ? 1 : 0.45 }}>
                <div className="row spread">
                  <div style={{ flex: 1 }}>
                    <div className="faint">
                      {row.description}
                      {row.confidence !== 'high' && (
                        <span style={{ color: 'var(--amber)' }}> · {row.confidence} confidence</span>
                      )}
                    </div>
                    {row.food ? (
                      row.options.length > 1 ? (
                        <select
                          className="input" style={{ minHeight: 40, marginTop: 4 }}
                          value={row.food.id}
                          onChange={(e) => update(row.key, { food: row.options.find((o) => o.id === Number(e.target.value)) })}
                        >
                          {row.options.map((o) => (
                            <option key={o.id} value={o.id}>{o.name}{o.brand ? ` — ${o.brand}` : ''}</option>
                          ))}
                        </select>
                      ) : <b>{row.food.name}</b>
                    ) : (
                      <div>
                        <div className="error">No catalog match.</div>
                        <div className="row">
                          <input className="input" style={{ minHeight: 40 }} placeholder="Search manually…"
                                 value={row.search}
                                 onChange={(e) => update(row.key, { search: e.target.value })} />
                          <button className="btn secondary small" onClick={() => rowSearch(row)}>Find</button>
                        </div>
                        {row.searchResults?.map((r) => (
                          <button key={r.id} className="food-row" style={{ minHeight: 44 }}
                                  onClick={() => update(row.key, {
                                    food: { id: r.id, name: r.name, brand: r.brand, per_100g: r.per_100g },
                                    options: [], searchResults: null, included: true,
                                  })}>
                            <span>{r.name}</span>
                          </button>
                        ))}
                        {row.searchResults?.length === 0 && <p className="faint">Nothing found — this item will be skipped.</p>}
                      </div>
                    )}
                    {row.reasoning && <div className="faint" style={{ marginTop: 4 }}>{row.reasoning}</div>}
                  </div>
                </div>
                {row.food && (
                  <div className="row spread" style={{ marginTop: 8 }}>
                    <div className="row">
                      <input className="input num" type="number" inputMode="decimal" min="1"
                             style={{ width: 90, minHeight: 40 }}
                             value={row.grams}
                             onChange={(e) => update(row.key, { grams: e.target.value })} />
                      <span className="muted">g</span>
                    </div>
                    <span className="muted num">{fmt(perRow(row, 'kcal'))} kcal · {fmt(perRow(row, 'sodium_mg'))} mg Na</span>
                    <button className="btn secondary small"
                            onClick={() => update(row.key, { included: !row.included })}>
                      {row.included ? 'Skip' : 'Include'}
                    </button>
                  </div>
                )}
              </div>
            ))}

            {rows.length > 0 && (
              <>
                <div className="preview num">
                  <span><b>{fmt(totals.kcal)}</b><span className="faint">kcal</span></span>
                  <span><b>{fmt(totals.protein_g, 1)}</b><span className="faint">protein g</span></span>
                  <span><b>{fmt(totals.sodium_mg)}</b><span className="faint">sodium mg</span></span>
                </div>
                <button className="btn" disabled={state.phase === 'saving' || toLog.length === 0} onClick={confirm}>
                  {state.phase === 'saving' ? 'Logging…' : `Log ${toLog.length} item${toLog.length === 1 ? '' : 's'}`}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
