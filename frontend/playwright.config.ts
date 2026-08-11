import { defineConfig, devices } from '@playwright/test';

// Real-browser end-to-end coverage for Contract Chat's SSE-driven UI, a
// class of bug (a client-side stream ending without a terminal event
// leaving the UI permanently stuck) that unit/component tests mocking
// fetchEventSource's callbacks directly cannot catch on their own - those
// tests prove the callback logic is correct in isolation, not that the
// real browser's fetch/ReadableStream plumbing actually reaches it the
// same way. Requires the real dev stack running (`docker compose up`,
// backend on BACKEND_PORT, ui on 3000) - not run as part of `npm test`.
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
