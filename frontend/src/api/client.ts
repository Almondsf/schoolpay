/**
 * Typed API client. Paths, request bodies and responses come from
 * `schema.d.ts`, generated from the backend's OpenAPI schema
 * (`npm run gen:api` after `python -m app.openapi > ../frontend/openapi.json`),
 * so the frontend and backend can't drift apart silently.
 *
 * The access token lives only in memory. The refresh token is an httpOnly
 * cookie scoped to /api/v1/auth that JavaScript can't read. A 401 triggers one
 * silent refresh and a retry; if that fails, the app is told the session ended.
 */
import createClient, { type Middleware } from 'openapi-fetch';

import type { components, paths } from './schema';

export type Schemas = components['schemas'];
export type Me = Schemas['Me'];
export type SignedIn = Schemas['SignedIn'];

/** The backend's single error shape. */
export interface ApiErrorBody {
  error: { code: string; message: string; field?: string | null; details?: unknown };
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly field?: string | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

// Unset in development (Vite proxies /api) and when the app is served from the API's origin
const baseUrl = import.meta.env.VITE_API_URL || globalThis.location.origin;

export const api = createClient<paths>({
  baseUrl,
  credentials: 'include',
  // Resolve fetch per call (not once at startup) so tests can stub it
  fetch: (request) => globalThis.fetch(request),
});

let accessToken: string | null = null;
let refreshing: Promise<{ access_token: string } | null> | null = null;
let onSessionExpired: () => void = () => {};

/** Staff and parents sign in separately; each has its own refresh cookie and endpoint. */
export type AuthRealm = 'staff' | 'parent';
const REFRESH_URL: Record<AuthRealm, string> = { staff: '/api/v1/auth/refresh', parent: '/api/v1/parent/auth/refresh' };
let realm: AuthRealm = 'staff';

export function setAuthRealm(next: AuthRealm): void {
  realm = next;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function setSessionExpiredHandler(handler: () => void): void {
  onSessionExpired = handler;
}

/** Exchange the refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession<T extends { access_token: string } = SignedIn>(): Promise<T | null> {
  refreshing ??= globalThis
    .fetch(`${baseUrl}${REFRESH_URL[realm]}`, { method: 'POST', credentials: 'include' })
    .then(async (response) => {
      if (!response.ok) return null;
      const body = (await response.json()) as T;
      setAccessToken(body.access_token);
      return body;
    })
    .catch(() => null)
    .finally(() => {
      refreshing = null;
    });
  return refreshing as Promise<T | null>;
}

// Endpoints where a 401 means "wrong credentials", not "token expired"
const NO_RETRY = ['/api/v1/auth/login', '/api/v1/auth/refresh', '/api/v1/auth/password/', '/api/v1/parent/auth/'];
const originals = new WeakMap<Request, Request>();

const authMiddleware: Middleware = {
  onRequest({ request }) {
    if (accessToken) request.headers.set('Authorization', `Bearer ${accessToken}`);
    originals.set(request, request.clone());
    return request;
  },
  async onResponse({ request, response }) {
    const path = new URL(request.url).pathname;
    if (response.status !== 401 || !accessToken || NO_RETRY.some((p) => path.startsWith(p))) return response;
    const retry = originals.get(request);
    if (!(await refreshSession()) || !retry) {
      setAccessToken(null);
      onSessionExpired();
      return response;
    }
    retry.headers.set('Authorization', `Bearer ${accessToken}`);
    return globalThis.fetch(retry);
  },
};
api.use(authMiddleware);

/** Unwrap an openapi-fetch result: return the data or throw an ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    const body = result.error as Partial<ApiErrorBody> | undefined;
    throw new ApiError(
      result.response.status,
      body?.error?.code ?? 'http_error',
      body?.error?.message ?? `Request failed (${result.response.status})`,
      body?.error?.field,
    );
  }
  return result.data as T;
}
