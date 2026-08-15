import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Phase 1 of the master-upgrade plan (showcase_readiness_audit.md's
// "Multimodal Uploads" finding): Contract Chat's image-redline-comparison
// path already existed (BASE_SYSTEM_PROMPT's anti-mirroring rule in
// contract_chat_agent.py already told the model to diff an attached
// redline image against retrieved contract text) and was already exercised
// by live_diagnostic_audit.spec.ts's Stage 5e, but only as a soft
// keyword-count WARN against the live production site's pre-seeded demo
// account. This is the same scenario hardened into a real local hard-fail
// assertion, self-contained: registers its own fresh tenant, uploads a
// real contract PDF, and proves the now-more-structured "Differences
// found:" response format added to BASE_SYSTEM_PROMPT's rule 3.
//
// Requires the real local dev stack (docker compose up) and real provider
// credentials configured for the backend - no network mocking, a real
// LLM call end to end, same as chat-cross-turn-image-context.spec.ts.

const MOCK_REDLINE = path.resolve(__dirname, 'fixtures/mock_redline.png');
const CLEAN_MSA_PDF = path.resolve(__dirname, '../../data/Clean_MSA.pdf');

test.describe.configure({ retries: 2 });

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_redline_${suffix}`;
  const tenantId = `e2e_redline_tenant_${suffix}`;
  const password = 'E2ETestPassword123!';

  await page.goto('/');
  await page.getByRole('button', { name: 'Need an account? Create one' }).click();
  await page.locator('#reg-username').fill(username);
  await page.locator('#reg-password').fill(password);
  await page.locator('#reg-tenant-id').fill(tenantId);
  // Registration form's role selector already defaults to ADMIN
  // (LoginScreen.tsx's regRole useState) - ADMIN has UPLOAD permission
  // (RBACManager.ROLE_PERMISSIONS), which this test needs to upload a
  // real contract PDF, so no role selection needed here.
  await page.getByRole('button', { name: 'Create account' }).click();

  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForSelector('text=Contract Chat', { timeout: 15000 });
}

async function uploadRealContract(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Document Analysis' }).click();
  await page.locator('input[type="file"]').setInputFiles(CLEAN_MSA_PDF);
  // Real (non-mocked) success text from DocumentUpload.tsx's
  // getStatusMessage - the enhanced-embeddings upload is synchronous, so
  // this text appearing means the contract is already fully indexed and
  // searchable, no extra polling needed.
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

test.describe('Contract Chat redline-image comparison (Phase 1 hardening)', () => {
  test('an attached redline image is diffed against the real contract text in a structured format', async ({ page }) => {
    test.setTimeout(180000);

    await registerAndSignIn(page);
    await uploadRealContract(page);

    await page.getByRole('button', { name: 'Contract Chat' }).click();
    await setScope(page, 'Clean_MSA.pdf');

    // Attach the redline image via the real Paperclip file picker.
    const attachBtn = page.locator('button[aria-label="Attach an image"]').first();
    await attachBtn.waitFor({ timeout: 8000 });
    await attachBtn.click();
    await page.locator('input[type="file"][accept*="image"]').first().setInputFiles(MOCK_REDLINE);
    await page.waitForTimeout(1000);

    await page.getByPlaceholder('Type your prompt here!').fill(
      'Does the indemnification language shown in this redlined image conflict with the ' +
      'indemnification clause in this contract? Explain any discrepancies.'
    );
    await page.getByRole('button', { name: /Send your prompt now/ }).click();
    await waitForGenerationToFinish(page);

    await expect(page.getByRole('alert')).toHaveCount(0);
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.toLowerCase()).not.toContain('blocked by the contract chat safety policy');
    expect(bodyText.toLowerCase()).not.toContain('no relevant contract evidence was found');
    expect(bodyText.toLowerCase()).not.toContain('no longer have access');

    // The AI's own message bubble - real, structured redline comparison
    // output required by contract_chat_agent.py's extended rule 3, not
    // just a keyword hit as Stage 5e originally checked.
    const aiMessage = page.locator('div:has(> strong:text-is("AI"))').last();
    const responseText = ((await aiMessage.textContent().catch(() => '')) ?? '').toLowerCase();
    expect(responseText).toContain('differences found');
    expect(responseText).toContain('indemnif');

    // The redline-comparison claim needs real document_text evidence for
    // the contract side (not image_attachment evidence alone - see
    // HallucinationValidator's guideline 6), so a real citation must be
    // present.
    const citationCount = await page.locator('aside[aria-label="Sources"] button').count().catch(() => 0);
    expect(citationCount, 'redline comparison must cite real retrieved contract text').toBeGreaterThan(0);
  });
});
