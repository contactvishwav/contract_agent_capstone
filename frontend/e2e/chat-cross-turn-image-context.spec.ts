import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ADR-008 follow-up (cross-turn image context): before this pass, an
// attached image was real context only for the exact turn it was
// attached to - a follow-up question about it on a LATER turn, with no
// re-attachment, got a model response declining to answer ("I no longer
// have access to that image"), even though this is not what a real user
// expects from a conversation. Fixed by carrying the single most recent
// image-bearing turn's real image forward into later turns (see
// backend/main.py's _messages_from_stored and
// backend/contract_chat_agent.py's _conversation_has_image_evidence).
//
// This test drives the REAL backend end to end (no network mocking,
// unlike chat-image-verifying-phase.spec.ts's fault-injection tests) -
// a real two-turn conversation against a real running LLM, proving the
// actual user-facing flow: attach an image, ask about it, then in a
// BRAND NEW message with NO re-attachment, ask a follow-up, and get a
// real, correct, visible answer. Requires the real dev stack running
// (docker compose up, backend reachable via the ui's Vite proxy, ui on
// port 3000) and real provider credentials configured for the backend.

const TEST_IMAGE = path.resolve(__dirname, 'fixtures/test-image.png');

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_crossturn_${suffix}`;
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

async function waitForGenerationToFinish(page: import('@playwright/test').Page, timeoutMs = 60000) {
  // Wait for generation to actually START first - checking only for
  // absence risks a race where the request hasn't rendered "Stop
  // generating" yet at all, resolving trivially for the wrong reason.
  await expect(page.locator('button:has-text("Stop generating")')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('button:has-text("Stop generating")')).toHaveCount(0, { timeout: timeoutMs });
}

// Real, quantified, unrelated source of flakiness: this drives the real
// audit-retry Output Guard chain end to end, with a directly measured
// ~1.7-4% false-rejection rate on genuinely correct answers (see the ADR-004
// addendum's "bounded retry" section) - a real occasional retry here
// absorbs that, same as the retry already built into the audit step
// itself, not a tolerance for a real bug in this feature.
test.describe.configure({ retries: 2 });

test.describe('Contract Chat cross-turn image context', () => {
  test('a follow-up with no re-attachment still gets a real, correct answer about the image', async ({ page }) => {
    test.setTimeout(120000);
    await registerAndSignIn(page);
    await page.getByRole('button', { name: 'Contract Chat' }).click();

    // Turn 1: attach a real image via the real file picker, ask about it.
    await page.locator('input[type="file"]').setInputFiles(TEST_IMAGE);
    await page.waitForSelector('[aria-label="Uploading"]', { state: 'detached', timeout: 30000 }).catch(() => {});
    await page.getByPlaceholder("Type your prompt here!").fill("What's in this image?");
    await page.getByRole('button', { name: /Send your prompt now/ }).click();

    await waitForGenerationToFinish(page);
    await expect(page.getByRole('alert')).toHaveCount(0);
    // Real answer confirms the model actually examined the real image.
    await expect(page.getByText(/circle/i)).toBeVisible();

    // Turn 2: a BRAND NEW message, no re-attachment at all, asking a
    // genuine follow-up about "the image."
    await page.getByPlaceholder("Type your prompt here!").fill('What color is the circle?');
    await page.getByRole('button', { name: /Send your prompt now/ }).click();

    await waitForGenerationToFinish(page);
    await expect(page.getByRole('alert')).toHaveCount(0, { timeout: 5000 });

    // A real, correct, visible answer - not a decline, not a hallucination.
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.toLowerCase()).not.toContain('no longer have access');
    expect(bodyText.toLowerCase()).not.toContain("don't have access");
    await expect(page.getByText(/blue/i).last()).toBeVisible();
  });
});
