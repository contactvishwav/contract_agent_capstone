import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Phase 4 of the master-upgrade plan (showcase_readiness_audit.md's "HITL
// Workflow" finding): requires_human_review used to be an unused field on
// three model classes, never set and never read - no pause/resume
// mechanism existed anywhere. This proves the real thing end to end
// through the UI: a HIGH/CRITICAL-risk contract pauses the LangGraph
// workflow at human_review_gate (Redis-backed checkpointer, so the pause
// survives across the backend/worker process split - see
// contract_intelligence_agents.py's _get_redis_checkpointer), an admin
// approves it from the review queue, and the graph resumes
// (cuad_mitigation -> redline_generation -> END) to a real completed
// result.
//
// Clean_MSA.pdf (already used by Phase 1/2/3's specs) is the "known
// HIGH/CRITICAL risk contract fixture" - empirically confirmed during
// Phase 2 verification to score 100/100 (CRITICAL): it's a deliberately
// one-sided MSA (unlimited liability, broad indemnification, IP
// forfeiture) that reliably trips several CRITICAL policy violations on
// top of an MSA's already-elevated base risk.
//
// The registration form's role selector defaults to ADMIN (LoginScreen.
// tsx's regRole useState) - the "dynamically created ADMIN user" the plan
// calls for is this same self-registered user; no separate second login
// is needed since register() already grants ADMIN.
//
// Requires the real local dev stack (docker compose up, including the
// `worker` service - contract intelligence analysis runs as a Celery
// task) and real provider credentials configured for the backend. No
// network mocking, real LLM calls end to end.

const CLEAN_MSA_PDF = path.resolve(__dirname, '../../data/Clean_MSA.pdf');

test.describe.configure({ retries: 1 });

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_hitl_${suffix}`;
  const tenantId = `e2e_hitl_tenant_${suffix}`;
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

async function uploadContract(page: import('@playwright/test').Page, pdfPath: string) {
  await page.getByRole('button', { name: 'Document Analysis' }).click();
  await page.locator('input[type="file"]').setInputFiles(pdfPath);
  await expect(page.getByText(/Contract created successfully/, { exact: false })).toBeVisible({ timeout: 60000 });
}

async function selectContract(page: import('@playwright/test').Page, filenameFragment: string) {
  const entry = page.getByRole('button', { name: new RegExp(filenameFragment, 'i') }).first();
  await entry.waitFor({ timeout: 15000 });
  await entry.click();
  await page.waitForTimeout(500);
}

test.describe('LangGraph HITL checkpointer (Phase 4)', () => {
  test('a HIGH/CRITICAL-risk contract pauses for review, and admin approval resumes it to completion', async ({ page }) => {
    test.setTimeout(600000);

    await registerAndSignIn(page);
    await uploadContract(page, CLEAN_MSA_PDF);
    await selectContract(page, 'clean_msa\\.pdf');

    await page.getByRole('button', { name: 'Analyze', exact: true }).click();

    // The full clause-extraction -> policy-check -> risk chain is several
    // real LLM calls before even reaching human_review_gate.
    const pendingCard = page.locator('[data-testid="pending-human-review-card"]');
    await expect(pendingCard).toBeVisible({ timeout: 240000 });
    await expect(pendingCard).toContainText(/HIGH|CRITICAL/);

    // "Analyze again" must NOT appear - the run is paused, not complete.
    await expect(page.getByRole('button', { name: 'Analyze again' })).toHaveCount(0);

    // Admin review queue (AccountPage.tsx's PendingReviewsSection).
    await page.locator('button[title="Account & security"]').click();
    await expect(page.getByText('Pending contract reviews')).toBeVisible({ timeout: 15000 });

    const reviewRow = page.locator('[data-testid="pending-review-row"]', { hasText: /clean_msa\.pdf/i });
    await expect(reviewRow).toBeVisible({ timeout: 15000 });
    await expect(reviewRow).toContainText(/HIGH|CRITICAL/);

    await reviewRow.getByRole('button', { name: 'Approve' }).click();

    // approve_review resumes the graph synchronously (real cuad_mitigation
    // + redline_generation LLM calls) - the row disappears from the queue
    // once that completes and the backend re-fetches the (now-empty) list.
    await expect(reviewRow).toHaveCount(0, { timeout: 240000 });
    await expect(page.getByText('No contracts are currently pending review.')).toBeVisible({ timeout: 15000 });

    // Back to the contract's own view - the resumed, now-complete result
    // (real redlines/risk data from redline_generation, not the paused
    // partial state) must render, not the pending-review card.
    await page.getByRole('button', { name: 'Document Analysis' }).click();
    await selectContract(page, 'clean_msa\\.pdf');

    await expect(page.locator('[data-testid="pending-human-review-card"]')).toHaveCount(0, { timeout: 15000 });
    await expect(page.getByText('Risk Score')).toBeVisible({ timeout: 30000 });
    await expect(page.getByRole('button', { name: 'Analyze again' })).toBeVisible();
  });
});
