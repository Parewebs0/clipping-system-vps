// Fetch helper: same origin as the FastAPI app; Bearer token kept in
// sessionStorage only (never localStorage), as the previous dashboard did.
const TOKEN_KEY = 'mc_token'
const listeners = new Set<() => void>()

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? ''
}

export function setToken(token: string): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
  listeners.forEach((l) => l())
}

export function onTokenChange(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI 422: [{loc, msg, type}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) =>
        [d.loc?.filter((x) => x !== 'body').join('.'), d.msg].filter(Boolean).join(': '),
      )
      .join('; ')
  }
  return fallback
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, { ...init, headers })
  if (res.status === 204) return undefined as T
  const text = await res.text()
  let body: unknown = undefined
  try {
    body = text ? JSON.parse(text) : undefined
  } catch {
    body = text
  }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | undefined)?.detail ?? body
    if (res.status === 401) {
      throw new ApiError(res.status, 'Token ausente o inválido', detail)
    }
    if (res.status === 403) {
      // API_WRITE_TOKEN configured and this token is read-only for campaign writes
      throw new ApiError(res.status, typeof detail === 'string' ? `Sin permiso de escritura: ${detail}` : 'Token ausente o sin permiso', detail)
    }
    throw new ApiError(res.status, detailToMessage(detail, `HTTP ${res.status}`), detail)
  }
  return body as T
}

export function qs(params: Record<string, string | number | undefined | null>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}
