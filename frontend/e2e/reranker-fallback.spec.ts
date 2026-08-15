import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Phase 3 of the master-upgrade plan (showcase_readiness_audit.md's
// "Cross-Encoder Reranking" finding): reranker_service.py already had
// strong resilience (circuit breaker, multi-provider fallback, timeout,
// malformed-response handling, all degrading to the original vector-order
// candidates) proven at the unit level by test_reranker_service.py. This
// proves the SAME thing through the real browser/UI: with a real reranker
// failure forced server-side (RERANKER_DEBUG_FORCE_FAILURE=1, a dev/test-
// only hook never reachable in production), a real search still returns
// real, visible results and citations - the user-facing feature keeps
// working, it doesn't error or go blank.
//
// Requires the local dev stack running with RERANKING_ENABLED=1 (off by
// default in docker-compose.yml) AND RERANKER_DEBUG_FORCE_FAILURE=1 set on
// the `backend` container for this run - see the master-upgrade plan's
// Phase 3 section for the exact docker compose invocation. No network
// mocking, a real LLM call end to end (just not a real reranking call,
// which is exactly what's under test).

const CLEAN_MSA_PDF = path.resolve(__dirname, '../../data/Clean_MSA.pdf');

test.describe.configure({ retries: 2 });

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_rerank_${suffix}`;
  const tenantId = `e2e_rerank_tenant_${suffix}`;
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

async function uploadRealContract(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Document Analysis' }).click();
  await page.locator('input[type="file"]').setInputFiles(CLEAN_MSA_PDF);
  await expect(page.getByText(/Contract created successfully/, { exact: false })).toBeVisible({ timeout: 60000 });
}

async function setScope(page: import('@playwright/test').Page, label: string) {
  const trigger = page.locator('[aria-label="Contract scope"]').first();
  await trigger.waitFor({ timeout: 8000 });
  await trigger.click();
  await page.waitForTimeout(500);
  const option = page.locator(`[role="option"]:has-text("${label}")`).first();
  await option.waitFor({ timeout: 8000 });
  await option.click();
  await page.waitForTimeout(500);
}

async function waitForGenerationToFinish(page: import('@playwright/test').Page, timeoutMs = 90000) {
  await expect(page.locator('button:has-text("Stop generating")')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('button:has-text("Stop generating")')).toHaveCount(0, { timeout: timeoutMs });
}

test.describe('Search stays usable when the reranker is broken (Phase 3)', () => {
  test('a real query still returns real results and citations with reranking force-failed', async ({ page }) => {
    test.setTimeout(180000);

    await registerAndSignIn(page);
    await uploadRealContract(page);

    await page.getByRole('button', { name: 'Contract Chat' }).click();
    await setScope(page, 'Clean_MSA.pdf');

    await page.getByPlaceholder('Type your prompt here!').fill(
      'What is the indemnification clause in this contract?'
    );
    await page.getByRole('button', { name: /Send your prompt now/ }).click();
    await waitForGenerationToFinish(page);

    // Same fail-closed checks as the other Phase specs - a reranker
    // failure must never surface as a guard rejection or an empty
    // "no evidence" answer instead of the (still-real, just unranked)
    // search results.
    await expect(page.getByRole('alert')).toHaveCount(0);
    const bodyText = (await page.locator('body').innerText()).toLowerCase();
    expect(bodyText).not.toContain('blocked by the contract chat safety policy');
    expect(bodyText).not.toContain('no relevant contract evidence was found');

    const aiMessage = page.locator('div:has(> strong:text-is("AI"))').last();
    const responseText = ((await aiMessage.textContent().catch(() => '')) ?? '').toLowerCase();
    expect(responseText.length, 'a real, substantive answer must still render').toBeGreaterThan(50);
    expect(responseText).toContain('indemnif');

    const citationCount = await page.locator('aside[aria-label="Sources"] button').count().catch(() => 0);
    expect(citationCount, 'real citations must still render with reranking broken').toBeGreaterThan(0);
  });
});
