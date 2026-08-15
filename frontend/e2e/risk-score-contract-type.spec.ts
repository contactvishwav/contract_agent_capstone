import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Phase 2 of the master-upgrade plan (showcase_readiness_audit.md's "Risk
// Analysis Integrity" finding): RiskCalculatorTool used to start every
// contract at the same flat risk_score = 30.0 regardless of its declared
// contract_type, and RiskDetail.tsx's "Risk Score Calculation" breakdown
// was independently fabricated client-side from score thresholds (fake
// "35%/25%/20%" weights, never the real formula). This proves, through the
// real UI against the real backend, that (a) two differently-typed
// contracts with no policy violations get different baseline scores, and
// (b) the breakdown panel renders the real, itemized score_breakdown the
// backend now returns instead of the old fabricated factors.
//
// Requires the real local dev stack (docker compose up, including the
// `worker` service - contract intelligence analysis runs as a Celery task,
// not inline in the request) and real provider credentials configured for
// the backend. No network mocking, real LLM calls end to end.

const CLEAN_NDA_PDF = path.resolve(__dirname, '../../data/Clean_NDA.pdf');
const CLEAN_MSA_PDF = path.resolve(__dirname, '../../data/Clean_MSA.pdf');

test.describe.configure({ retries: 1 });

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_risk_${suffix}`;
  const tenantId = `e2e_risk_tenant_${suffix}`;
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

async function selectAndAnalyze(page: import('@playwright/test').Page, filenameFragment: string) {
  // "Recent Contracts" list entry - real filename as stored (lowercased
  // by the upload pipeline), so a case-insensitive match.
  const entry = page.getByRole('button', { name: new RegExp(filenameFragment, 'i') }).first();
  await entry.waitFor({ timeout: 15000 });
  await entry.click();
  await page.waitForTimeout(500);

  const analyzeBtn = page.getByRole('button', { name: 'Analyze', exact: true });
  await analyzeBtn.waitFor({ timeout: 10000 });
  await analyzeBtn.click();

  // The full clause-extraction -> policy-check -> risk -> cuad -> redline
  // chain is several real LLM calls; give it a wide berth.
  await expect(page.getByRole('button', { name: 'Analyze again' })).toBeVisible({ timeout: 240000 });
}

async function readRiskScore(page: import('@playwright/test').Page): Promise<number> {
  const scoreText = await page.locator('text=/\\d+\\/100/').first().textContent();
  const match = scoreText?.match(/(\d+)\/100/);
  if (!match) throw new Error(`Could not parse risk score from "${scoreText}"`);
  return Number(match[1]);
}

test.describe('Risk score is contract-type-aware with a real breakdown (Phase 2)', () => {
  test('an NDA gets a lower baseline score than an MSA, with a real itemized breakdown', async ({ page }) => {
    test.setTimeout(600000);

    await registerAndSignIn(page);

    await uploadContract(page, CLEAN_NDA_PDF);
    await selectAndAnalyze(page, 'clean_nda\\.pdf');
    const ndaScore = await readRiskScore(page);

    await uploadContract(page, CLEAN_MSA_PDF);
    await selectAndAnalyze(page, 'clean_msa\\.pdf');
    const msaScore = await readRiskScore(page);

    console.log(`[RISK] NDA=${ndaScore} MSA=${msaScore}`);
    // Real gap is base 10 vs base 40 (30 points) before any violations;
    // a generous margin absorbs any real policy violations found on
    // either document without the assertion becoming exact-value-brittle.
    expect(ndaScore, 'NDA should score meaningfully lower than an MSA with the same violation profile').toBeLessThan(msaScore);

    // Open the Risk Score detail modal (still showing the MSA, the last
    // one analyzed) and assert the real, itemized backend breakdown
    // renders - not the old fabricated "Contract Complexity ... 20%"
    // style entries.
    await page.locator('text=/\\d+\\/100/').first().click();
    await expect(page.getByText('Risk Score Calculation')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/deterministically/i)).toBeVisible();
    await expect(page.getByText(/Base risk \(contract type/i)).toBeVisible();
    await expect(page.getByText('Points', { exact: true }).first()).toBeVisible();
    // The old fabricated breakdown always included this exact label -
    // its absence confirms the real backend data replaced it.
    await expect(page.getByText('Contract Complexity')).toHaveCount(0);
  });
});
