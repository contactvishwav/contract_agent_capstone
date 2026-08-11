import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Real, confirmed live bug: Contract Chat's image feature was reported
// hanging forever on "Verifying response..." in a real browser, after
// server-side fixes to the same class of problem had already landed.
// Real Playwright reproduction (login, select a contract, attach a real
// image via the file picker, ask a question, send) initially could not
// reproduce a hang under healthy network conditions - 18 consecutive real
// runs all completed. The actual defect only surfaces when the SSE
// connection's underlying stream ends WITHOUT ever delivering a terminal
// event (a dropped connection, a proxy truncation, a dev-server reload
// killing an in-flight request, or any other real-world cause) - proven
// live via page.route() truncating a real request's response body right
// after a real "verifying" status event, the same technique this test
// uses. input.tsx's onclose() handler had no fallback at all (unlike
// onerror()), so the affected message was left permanently showing
// "Verifying response..." even though the request machinery itself
// (Stop Generating, the Send button) had already cleaned up - the
// client-side mirror of the exact gap _guaranteed_terminal_stream
// (backend/main.py) closes server-side. The backend's own guarantee that
// it always SENDS a terminal event is meaningless if the client's
// connection ends before that event is actually delivered.
//
// Requires the real dev stack running (docker compose up, backend on
// BACKEND_PORT via the ui's Vite proxy, ui on port 3000). Registers its
// own ephemeral tenant/user via the real UI so this test is self
// contained and does not depend on any pre-existing seeded account.

const TEST_IMAGE = path.resolve(__dirname, 'fixtures/test-image.png');

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_verifying_${suffix}`;
  const tenantId = `e2e_tenant_${suffix}`;
  const password = 'E2ETestPassword123!';

  await page.goto('/');
  await page.getByRole('button', { name: 'Need an account? Create one' }).click();
  await page.locator('#reg-username').fill(username);
  await page.locator('#reg-password').fill(password);
  await page.locator('#reg-tenant-id').fill(tenantId);
  await page.getByRole('button', { name: 'Create account' }).click();

  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForSelector('text=Contract Chat', { timeout: 15000 });
}

async function openChatAndAttachImage(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Contract Chat' }).click();
  await page.locator('input[type="file"]').setInputFiles(TEST_IMAGE);
  await page.waitForSelector('[aria-label="Uploading"]', { state: 'detached', timeout: 30000 }).catch(() => {});
  await page.getByPlaceholder("Type your prompt here!").fill("What's in this image?");
}

test.describe('Contract Chat image question - verifying phase', () => {
  test('a real answer renders in the DOM after generating and verifying phases', async ({ page }) => {
    await registerAndSignIn(page);
    await openChatAndAttachImage(page);

    // A realistic, complete SSE sequence, exactly as the real backend
    // produces it (see backend/main.py:runner) - generation's own
    // content is buffered until Output Guard passes, so the client only
    // ever sees the "verifying" status event and then the final
    // ai_message/end pair, never partial answer tokens.
    await page.route('**/api/run/', async (route) => {
      const events = [
        { content: '', type: 'status', phase: 'verifying' },
        {
          content: 'The image contains a blue circle and an orange square.',
          type: 'ai_message', status: 'passed',
          requested_model: 'gemini-2.5-flash', actual_model: 'gemini-2.5-flash',
          requested_provider: 'google', actual_provider: 'google',
          fallback_occurred: false, prompt_version: 'contract-chat-v2-evidence',
          execution_path: 'contract_chat_langgraph',
        },
        { content: '', type: 'end', status: 'passed', reason_category: 'none' },
      ];
      const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body });
    });

    await page.getByRole('button', { name: /Send your prompt now/ }).click();

    // The real, visible, correct answer text - not just "something
    // rendered" - actually appears in the DOM.
    await expect(page.getByText('The image contains a blue circle and an orange square.')).toBeVisible({ timeout: 10000 });

    // The verifying phase resolved cleanly - no stuck spinner/label left
    // behind, Stop Generating gone, Send available again.
    await expect(page.getByText('Verifying response')).toHaveCount(0);
    await expect(page.locator('button:has-text("Stop generating")')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Send your prompt now/ })).toBeEnabled();
  });

  test('a stream that ends without a terminal event resolves to a clear error, not a permanent hang', async ({ page }) => {
    await registerAndSignIn(page);
    await openChatAndAttachImage(page);

    // Simulates a dropped connection / proxy truncation: the real
    // "verifying" event is delivered, then the response body just ends -
    // no ai_message, no error, no end event. This is exactly what a
    // network-level or dev-server-reload interruption looks like to the
    // browser; only the network layer is faked here, every other part of
    // this test drives the real running application.
    await page.route('**/api/run/', async (route) => {
      const body = `data: ${JSON.stringify({ content: '', type: 'status', phase: 'verifying' })}\n\n`;
      await route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body });
    });

    await page.getByRole('button', { name: /Send your prompt now/ }).click();

    // Must NOT remain permanently stuck on "Verifying response..." - the
    // real bug this test guards against.
    await expect(page.getByText('Response failed before completion. Please retry.')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Verifying response')).toHaveCount(0);
    await expect(page.locator('button:has-text("Stop generating")')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Send your prompt now/ })).toBeEnabled();
  });
});
