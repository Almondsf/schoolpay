/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// In development the API is proxied, so the app and API share an origin and
// the SameSite=Strict refresh-token cookie behaves exactly as in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    strictPort: true,
    proxy: { '/api': process.env.API_TARGET ?? 'http://127.0.0.1:8002' },
  },
  // Browser tests in e2e/ run with Playwright (npm run e2e), not Vitest
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
    testTimeout: 20_000,
  },
});
