import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { addDays, localToday, prettyDay } from '../dates.js'

const fmt = (v, d = 0) => (v == null ? '—' : Number(v).toFixed(d))

function TargetLine({ label, logged, target, unit, digits = 0 }) {
  if (target == null) return null
  const pct = Math.min(100, ((logged ?? 0) / target) * 100)
  const over = (logged ?? 0) > target
  return (
    <div className="target-line num">
      <span className="label">{label}</span>
      <div className="bar"><div className={over ? 'over' : ''} style={{ width: `${pct}%` }} /></div>
      <span className="remaining" style={over ? { color: 'var(--red)' } : {}}>
        {fmt(logged, digits)} / {fmt(target, digits)}{unit}
      </span>
    </div>
  )
}

/* Day view: single chronological list (decision 7); remaining sodium and kcal
   lead (decision 8). Coverage rides with every number — a low sodium total
   with 60% coverage is labeled as such, never passed off as complete. */
export default function DayView({ dataVersion, onChanged }) {
  const [day, setDay] = useState(localToday())
  const [summary, setSummary] = useState(null)
  const [entries, setEntries] = useState([])
  const [editing, setEditing] = useState(null)   // entry being edited
  const [error, setError] = useState(null)

  const load = () => {
    api(`/api/summary/${day}`).then(setSummary).catch((e) => setError(e.message))
    api(`/api/log/${day}`).then((d) => setEntries(d.entries)).catch((e) => setError(e.message))
  }
  useEffect(load, [day, dataVersion])

  const n = summary?.nutrients ?? {}
  const cov = (k) => n[k]?.coverage ?? 0
  const covNote = (k) =>
    cov(k) > 0 && cov(k) < 0.999 ? ` (${Math.round(cov(k) * 100)}% of grams report it)` : ''

  const remove = async (entry) => {
    try {
      await api(`/api/log/${entry.id}`, { method: 'DELETE' })
      setEditing(null)
      load()
      onChanged()
    } catch (e) { setError(e.message) }
  }

  const saveGrams = async (entry, grams) => {
    try {
      await api(`/api/log/${entry.id}`, { method: 'PATCH', body: { grams: Number(grams) } })
      setEditing(null)
      load()
      onChanged()
    } catch (e) { setError(e.message) }
  }

  return (
    <div className="view">
      <div className="day-nav">
        <button onClick={() => setDay(addDays(day, -1))}>‹</button>
        <h1 style={{ margin: 0 }}>{prettyDay(day)}</h1>
        <button onClick={() => setDay(addDays(day, 1))} disabled={day >= localToday()}>›</button>
      </div>
      {error && <div className="error" onClick={() => setError(null)}>{error} (tap to dismiss)</div>}

      {summary && (
        <div className="card">
          {summary.remaining ? (
            <>
              <div className="row spread" style={{ marginBottom: 12 }}>
                {summary.remaining.kcal != null && (
                  <div>
                    <span className="big-remaining num" style={summary.remaining.kcal < 0 ? { color: 'var(--red)' } : {}}>
                      {fmt(Math.abs(summary.remaining.kcal))}
                    </span>
                    <div className="muted">kcal {summary.remaining.kcal < 0 ? 'over' : 'left'}</div>
                  </div>
                )}
                {summary.remaining.sodium_mg != null && (
                  <div style={{ textAlign: 'right' }}>
                    <span className="big-remaining num" style={summary.remaining.sodium_mg < 0 ? { color: 'var(--red)' } : {}}>
                      {fmt(Math.abs(summary.remaining.sodium_mg))}
                    </span>
                    <div className="muted">mg sodium {summary.remaining.sodium_mg < 0 ? 'over' : 'left'}{covNote('sodium_mg')}</div>
                  </div>
                )}
              </div>
              <div className="target-block">
                <TargetLine label="kcal" logged={n.kcal?.total} target={summary.target.kcal} unit="" />
                <TargetLine label="protein" logged={n.protein_g?.total} target={summary.target.protein_g} unit="g" />
                <TargetLine label="sodium" logged={n.sodium_mg?.total} target={summary.target.sodium_mg} unit="mg" />
                <TargetLine label="carbs" logged={n.carbs_g?.total} target={summary.target.carbs_g} unit="g" />
                <TargetLine label="fat" logged={n.fat_g?.total} target={summary.target.fat_g} unit="g" />
                <TargetLine label="fiber" logged={n.fiber_g?.total} target={summary.target.fiber_g} unit="g" />
              </div>
            </>
          ) : (
            <>
              <h2>Totals</h2>
              <div className="preview num">
                <span><b>{fmt(n.kcal?.total)}</b><span className="faint">kcal</span></span>
                <span><b>{fmt(n.protein_g?.total, 1)}</b><span className="faint">protein g</span></span>
                <span><b>{fmt(n.sodium_mg?.total)}</b><span className="faint">sodium mg{covNote('sodium_mg')}</span></span>
              </div>
              <p className="faint">No targets set yet — set them via the API (PUT /api/targets); a settings screen is on the roadmap.</p>
            </>
          )}
        </div>
      )}

      <div className="card">
        <h2>{summary?.n_entries ?? 0} entries · {fmt(summary?.total_grams)}g</h2>
        {entries.map((e) => (
          <button key={e.id} className="entry" onClick={() => setEditing(e)}>
            <span>
              {e.food?.name}
              <div className="when num">
                {new Date(e.logged_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
                {' · '}{fmt(e.grams)}g
                {e.entry_method !== 'manual' && ` · ${e.entry_method.replace('_', ' ')}`}
              </div>
            </span>
            <span className="kcal num">
              {fmt(e.per_entry?.kcal)} kcal
              <div className="when">{fmt(e.per_entry?.sodium_mg)} mg Na</div>
            </span>
          </button>
        ))}
        {entries.length === 0 && <p className="muted">Nothing logged on this day.</p>}
      </div>

      {editing && (
        <EditSheet entry={editing} onClose={() => setEditing(null)} onSave={saveGrams} onDelete={remove} />
      )}
    </div>
  )
}

function EditSheet({ entry, onClose, onSave, onDelete }) {
  const [grams, setGrams] = useState(entry.grams)
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="row spread">
          <b>{entry.food?.name}</b>
          <button className="btn secondary small" onClick={onClose}>Close</button>
        </div>
        <div className="qty-row" style={{ marginTop: 14 }}>
          <input className="input num" type="number" inputMode="decimal" min="1"
                 value={grams} onChange={(e) => setGrams(e.target.value)} />
          <span className="muted">g</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn" onClick={() => onSave(entry, grams)} disabled={!grams || grams <= 0}>Save</button>
          <button className="btn danger" style={{ width: 'auto' }} onClick={() => onDelete(entry)}>Delete</button>
        </div>
      </div>
    </div>
  )
}
