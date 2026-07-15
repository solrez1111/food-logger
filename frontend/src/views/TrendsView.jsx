import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { addDays, localToday } from '../dates.js'
import { Bars, Line } from '../components/charts.jsx'

const NUTRIENT_CHOICES = [
  ['sodium_mg', 'Sodium (mg)'],
  ['potassium_mg', 'Potassium (mg)'],
  ['magnesium_mg', 'Magnesium (mg)'],
  ['calcium_mg', 'Calcium (mg)'],
  ['fiber_g', 'Fiber (g)'],
  ['sugars_total_g', 'Total sugars (g)'],
  ['fatty_acids_total_saturated_g', 'Saturated fat (g)'],
  ['cholesterol_mg', 'Cholesterol (mg)'],
  ['caffeine_mg', 'Caffeine (mg)'],
  ['alcohol_g', 'Alcohol (g)'],
]

/* Trends: kcal + protein bars (7/30d), weight with 7-day smoothing (lbs —
   PLAN context), micronutrient panel where bar opacity IS the coverage
   (dim bar = thin data, honest by construction). */
export default function TrendsView({ dataVersion }) {
  const [span, setSpan] = useState(7)
  const [days, setDays] = useState([])
  const [weights, setWeights] = useState([])
  const [nutrient, setNutrient] = useState('sodium_mg')
  const [nutrientDays, setNutrientDays] = useState([])
  const [error, setError] = useState(null)

  const today = localToday()
  const start = addDays(today, -(span - 1))
  const weightStart = addDays(today, -89)

  useEffect(() => {
    api(`/api/summary?start=${start}&end=${today}`)
      .then((d) => setDays(d.days)).catch((e) => setError(e.message))
    api(`/api/weight?start=${weightStart}&end=${today}`)
      .then((d) => setWeights(d.weights)).catch((e) => setError(e.message))
  }, [span, dataVersion])

  useEffect(() => {
    api(`/api/summary/nutrient/${nutrient}?start=${addDays(today, -29)}&end=${today}`)
      .then((d) => setNutrientDays(d.days)).catch((e) => setError(e.message))
  }, [nutrient, dataVersion])

  // continuous day axis; unlogged days appear as gaps, not zeros
  const axis = []
  for (let i = 0; i < span; i++) axis.push(addDays(start, i))
  const byDate = Object.fromEntries(days.map((d) => [d.date, d]))
  const barData = (key) => axis.map((d, i) => ({
    label: d.slice(5),
    showLabel: span === 7 ? true : i % 5 === 0,
    value: byDate[d]?.nutrients?.[key]?.total ?? null,
    alpha: byDate[d]?.nutrients?.[key]?.coverage ?? 1,
  }))

  // weight: raw points + trailing 7-day mean overlay
  const wPoints = weights.map((w, i) => ({ x: weights.length === 1 ? 0.5 : i / (weights.length - 1), y: w.weight_lb }))
  const wSmooth = weights.map((w, i) => {
    const window = weights.slice(Math.max(0, i - 6), i + 1)
    return { x: wPoints[i].x, y: window.reduce((a, b) => a + b.weight_lb, 0) / window.length }
  })

  const nutrientAxis = []
  for (let i = 0; i < 30; i++) nutrientAxis.push(addDays(today, -(29 - i)))
  const nByDate = Object.fromEntries(nutrientDays.map((d) => [d.date, d]))
  const nutrientBars = nutrientAxis.map((d, i) => ({
    label: d.slice(5), showLabel: i % 5 === 0,
    value: nByDate[d]?.total ?? null,
    alpha: nByDate[d]?.coverage ?? 1,
  }))
  const nCoverages = nutrientDays.filter((d) => d.total != null).map((d) => d.coverage)
  const avgCoverage = nCoverages.length
    ? Math.round((nCoverages.reduce((a, b) => a + b, 0) / nCoverages.length) * 100) : null

  return (
    <div className="view">
      <h1>Trends</h1>
      {error && <div className="error" onClick={() => setError(null)}>{error} (tap to dismiss)</div>}

      <div className="seg" style={{ marginBottom: 12 }}>
        <button className={span === 7 ? 'active' : ''} onClick={() => setSpan(7)}>7 days</button>
        <button className={span === 30 ? 'active' : ''} onClick={() => setSpan(30)}>30 days</button>
      </div>

      <div className="card">
        <h2>Calories</h2>
        {days.length ? <Bars data={barData('kcal')} /> : <p className="muted">No logged days in range yet.</p>}
      </div>

      <div className="card">
        <h2>Protein (g)</h2>
        {days.length ? <Bars data={barData('protein_g')} color="var(--green)" /> : <p className="muted">No logged days in range yet.</p>}
      </div>

      <div className="card">
        <h2>Weight (lb, 90 days)</h2>
        {weights.length >= 2 ? (
          <>
            <Line points={wPoints} smoothed={wSmooth} format={(v) => v.toFixed(1)} />
            <div className="legend">
              <span><span className="dot" style={{ background: 'var(--text-dim)' }} /> daily</span>
              <span><span className="dot" style={{ background: 'var(--accent)' }} /> 7-day average</span>
            </div>
          </>
        ) : (
          <p className="muted">Weight trend appears after 2+ logged weights (n={weights.length}).</p>
        )}
      </div>

      <div className="card">
        <h2>Nutrient (30 days)</h2>
        <select className="input" value={nutrient} onChange={(e) => setNutrient(e.target.value)}
                style={{ marginBottom: 10 }}>
          {NUTRIENT_CHOICES.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
        {nutrientDays.some((d) => d.total != null) ? (
          <>
            <Bars data={nutrientBars} color="var(--amber)" />
            <p className="coverage-note">
              Bar brightness = coverage (share of logged grams that report this nutrient
              {avgCoverage != null && `; avg ${avgCoverage}%`}). Dim bars mean thin data, not low intake.
            </p>
          </>
        ) : (
          <p className="muted">No {NUTRIENT_CHOICES.find(([k]) => k === nutrient)?.[1]} data in the last 30 days.</p>
        )}
      </div>
    </div>
  )
}
