// Fetch wrapper: bearer token from localStorage, JSON both ways.
// 401 clears the token so the app falls back to the setup screen.

const TOKEN_KEY = 'food-log-token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `API error ${status}`)
    this.status = status
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: `Bearer ${getToken() ?? ''}` }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined })
  } catch (err) {
    const e = new ApiError(0, 'offline')
    e.cause = err
    throw e
  }
  if (res.status === 401) {
    clearToken()
    throw new ApiError(401, 'not authenticated')
  }
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, data.detail || data.error)
  return data
}
