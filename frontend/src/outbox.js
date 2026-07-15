// IndexedDB outbox for failed log POSTs (PLAN decision 5).
// iOS WebKit has no Background Sync — we queue here and flush on app open,
// foreground, and network recovery. Every queued body carries its client_id,
// so a retry that raced a success is deduplicated server-side.

import { api } from './api.js'

const DB = 'food-log'
const STORE = 'outbox'

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'client_id' })
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function tx(mode, fn) {
  const db = await openDb()
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode)
    const out = fn(t.objectStore(STORE))
    t.oncomplete = () => resolve(out.result ?? out)
    t.onerror = () => reject(t.error)
  })
}

export const enqueue = (logBody) => tx('readwrite', (s) => s.put(logBody))
export const remove = (clientId) => tx('readwrite', (s) => s.delete(clientId))

export function pending() {
  return tx('readonly', (s) => s.getAll()).then((r) => r ?? [])
}

let flushing = false

// Returns the number of entries that made it to the server.
export async function flush(onChange) {
  if (flushing) return 0
  flushing = true
  let sent = 0
  try {
    for (const body of await pending()) {
      try {
        await api('/api/log', { method: 'POST', body })   // idempotent on client_id
        await remove(body.client_id)
        sent += 1
        onChange?.()
      } catch (err) {
        if (err.status === 0) break        // still offline — keep the rest queued
        if (err.status === 401) break      // needs re-auth; keep queued
        // 4xx = the entry itself is bad (deleted food, bad shape) — drop it
        // rather than retry forever; the server rejected it deterministically.
        if (err.status >= 400 && err.status < 500) {
          await remove(body.client_id)
          onChange?.()
        }
      }
    }
  } finally {
    flushing = false
  }
  return sent
}

export function startAutoFlush(onChange) {
  const kick = () => flush(onChange)
  window.addEventListener('online', kick)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') kick()
  })
  kick()
}
