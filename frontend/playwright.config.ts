/**
 * Browser (end-to-end) tests: `npm run e2e`.
 *
 * Starts its own API and Vite servers on separate ports against a throwaway
 * database (E2E_DATABASE_URL, default schoolpay_e2e on localhost) seeded by
 * `python -m app.cli seed-demo --reset`, so it never touches development data.
 *
 * Environment:
 *   PYTHON            Python with the backend installed (default: the backend venv)
 *   E2E_DATABASE_URL  throwaway Postgres database for the run
 *   PW_CHANNEL        use an installed browser instead of Playwright's Chromium, e.g. msedge on Windows
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

const BACKEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'backend');
const API_PORT = 8102;
const WEB_PORT = 5185;
const venvPython =
  process.platform === 'win32' ? path.join(BACKEND, '.venv', 'Scripts', 'python.exe') : path.join(BACKEND, '.venv', 'bin', 'python');
const python = `"${process.env.PYTHON ?? venvPython}"`;
export const E2E_MFA_SECRET = 'JBSWY3DPEHPK3PXP';

const backendEnv = {
  ...process.env,
  SCHOOLPAY_ENV_FILE: 'does-not-exist.env', // never read a developer's .env
  ENVIRONMENT: 'development', // plain-http localhost cookies
  SECRET_KEY: 'e2e-only-signing-key-0123456789abcdef0123456789',
  FIELD_ENCRYPTION_KEYS: 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=',
  DATABASE_URL: process.env.E2E_DATABASE_URL ?? 'postgresql+psycopg://schoolpay@localhost:5433/schoolpay_e2e',
  CELERY_TASK_ALWAYS_EAGER: 'true',
  EMAIL_BACKEND: 'console',
  PAYMENT_GATEWAYS: 'fake', // simulated gateway: no real money moves
  SMS_BACKEND: 'memory', // texts are kept in memory; tests read sign-in codes from /api/v1/dev/sms/latest
  // Every test signs in from the same address
  LOGIN_RATE_PER_IP: '1000/minute',
  LOGIN_RATE_PER_ACCOUNT: '1000/hour',
  CORS_ORIGINS: `http://localhost:${WEB_PORT}`,
  FRONTEND_URL: `http://localhost:${WEB_PORT}`,
} as Record<string, string>;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    channel: process.env.PW_CHANNEL || undefined,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } } },
  ],
  webServer: [
    {
      command: `${python} -m app.core.migrate && ${python} -m app.cli seed-demo --reset --mfa-secret ${E2E_MFA_SECRET} && ${python} -m uvicorn app.main:app --port ${API_PORT}`,
      cwd: BACKEND,
      env: backendEnv,
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: 'ignore',
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      env: { ...process.env, API_TARGET: `http://127.0.0.1:${API_PORT}` } as Record<string, string>,
      url: `http://localhost:${WEB_PORT}`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});
