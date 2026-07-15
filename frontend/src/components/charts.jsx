import React from 'react'

/* Tiny hand-rolled SVG charts — no chart library (keeps the bundle lean and
   stays inside the agreed stack). Bars support per-bar opacity = coverage
   shading; the line chart draws raw points plus a smoothed overlay. */

export function Bars({ data, width = 600, height = 140, color = 'var(--accent)', format = (v) => v }) {
  // data: [{label, value, alpha?}]
  const max = Math.max(1, ...data.map((d) => d.value ?? 0))
  const bw = width / Math.max(1, data.length)
  return (
    <svg viewBox={`0 0 ${width} ${height + 22}`} style={{ width: '100%', height: 'auto' }}
         role="img" aria-label={`bar chart, max ${format(max)}`}>
      {data.map((d, i) => {
        const h = ((d.value ?? 0) / max) * (height - 14)
        return (
          <g key={i}>
            <rect
              x={i * bw + 2} y={height - h} width={Math.max(2, bw - 4)} height={h}
              rx="3" fill={color} opacity={d.value == null ? 0 : 0.35 + 0.65 * (d.alpha ?? 1)}
            />
            {d.showLabel && (
              <text x={i * bw + bw / 2} y={height + 16} textAnchor="middle"
                    fontSize="11" fill="var(--text-faint)">{d.label}</text>
            )}
          </g>
        )
      })}
    </svg>
  )
}

export function Line({ points, smoothed, width = 600, height = 150, format = (v) => v }) {
  // points: [{x: 0..1, y: value}]; smoothed: same shape, drawn bolder
  const ys = [...points, ...(smoothed ?? [])].map((p) => p.y)
  if (!ys.length) return null
  const min = Math.min(...ys)
  const max = Math.max(...ys)
  const pad = Math.max(0.5, (max - min) * 0.15)
  const yTo = (v) => height - ((v - (min - pad)) / (max + pad - (min - pad))) * height
  const path = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${(p.x * width).toFixed(1)},${yTo(p.y).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${width} ${height + 20}`} style={{ width: '100%', height: 'auto' }}
         role="img" aria-label={`line chart from ${format(min)} to ${format(max)}`}>
      <text x="2" y="12" fontSize="11" fill="var(--text-faint)">{format(max)}</text>
      <text x="2" y={height - 2} fontSize="11" fill="var(--text-faint)">{format(min)}</text>
      <path d={path(points)} fill="none" stroke="var(--text-faint)" strokeWidth="1.2" />
      {points.map((p, i) => (
        <circle key={i} cx={p.x * width} cy={yTo(p.y)} r="2.4" fill="var(--text-dim)" />
      ))}
      {smoothed && smoothed.length > 1 && (
        <path d={path(smoothed)} fill="none" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
      )}
    </svg>
  )
}
