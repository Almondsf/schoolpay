import { vi } from 'vitest';

type Handler = (request: Request) => Response | Promise<Response>;

/**
 * Stub global fetch with handlers keyed by "METHOD /path". Unmatched calls
 * fail the test loudly. Returns the list of requests made, for assertions.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Request[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init);
      calls.push(request.clone());
      const key = `${request.method} ${new URL(request.url).pathname}`;
      const handler = routes[key];
      if (!handler) throw new Error(`Unexpected request: ${key}`);
      return handler(request);
    }),
  );
  return calls;
}

export const json = (body: unknown, status = 200) => Response.json(body, { status });

export const error = (status: number, code: string, message = code) => json({ error: { code, message } }, status);

export function me(overrides: Record<string, unknown> = {}) {
  return {
    id: 'u1',
    email: 'ada@school.ng',
    full_name: 'Ada Obi',
    role: 'school_admin',
    school: { id: 's1', name: 'Grace Model College', slug: 'grace-model' },
    capabilities: ['school.view', 'school.manage', 'staff.view', 'students.view'],
    mfa_enabled: false,
    mfa_enrolment_required: false,
    ...overrides,
  };
}

export function signedIn(user = me()) {
  return { status: 'signed_in', access_token: 'access-1', token_type: 'bearer', expires_in: 900, user };
}
