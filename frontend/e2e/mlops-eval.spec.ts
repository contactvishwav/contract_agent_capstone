import { test, expect } from '@playwright/test';

// Phase 5 of the MASTER UPGRADE PART 2 plan (docs/tasks/active/
// mlops-eval-and-dynamic-routing.md): proves the offline retrieval
// evaluation harness (backend/scripts/evaluate_retrieval.py, golden
// dataset at backend/tests/evals/golden_dataset.json) is actually visible
// to a real ADMIN user, not just a script that writes a JSON file nobody
// reads. Requires the real local dev stack (docker compose up) with
// evaluate_retrieval.py already having been run at least once so GET
// /api/admin/evaluations has a real results artifact to serve.
//
// The registration form's role selector defaults to ADMIN (LoginScreen.tsx's
// regRole useState) - same convention as hitl-workflow.spec.ts, no separate
// admin login step needed.

test.describe.configure({ retries: 1 });

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_eval_${suffix}`;
  const tenantId = `e2e_eval_tenant_${suffix}`;
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

test.describe('MLOps offline evaluation dashboard (Phase 5)', () => {
  test('an ADMIN can see Recall@K and nDCG@K metrics for the golden retrieval dataset', async ({ page }) => {
    test.setTimeout(120000);

    await registerAndSignIn(page);

    await page.getByRole('button', { name: 'Evaluations' }).click();
    await expect(page.getByTestId('admin-evaluations-page')).toBeVisible({ timeout: 15000 });

    const recallMetric = page.getByTestId('eval-recall-metric');
    const ndcgMetric = page.getByTestId('eval-ndcg-metric');

    await expect(recallMetric).toBeVisible({ timeout: 15000 });
    await expect(ndcgMetric).toBeVisible();
    await expect(recallMetric).toContainText('%');
    await expect(ndcgMetric).toContainText('%');

    await expect(page.getByTestId('eval-per-query-table')).toBeVisible();
    await expect(page.getByText('golden queries', { exact: false })).toBeVisible();

    await page.screenshot({ path: 'test-results/phase5_eval_metrics.png', fullPage: true });
  });
});
