import { test, expect } from '@playwright/test';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Phase 6 of the MASTER UPGRADE PART 2 plan (docs/tasks/active/
// mlops-eval-and-dynamic-routing.md): proves the semantic student/teacher
// router (backend/routing_service.py) actually drives what a real user
// sees, not just that classify_complexity() returns the right string in a
// unit test. Selects "Autonomous Routing" from the real model dropdown
// (backend/api/model_registry_api.py's new "auto" pseudo-entry), sends one
// simple-extraction prompt and one redline/synthesis prompt through the
// real backend (real Gemini/Anthropic calls, no mocking), and asserts the
// ModelTierBadge (frontend/src/components/features/contracts/
// ModelTierBadge.tsx) shows the tier that actually answered.
//
// A real contract is uploaded and the chat session is scoped to it first
// (same setup as redline-image-comparison.spec.ts): the redline/synthesis
// prompt needs real retrieved evidence to pass Output Guard's fail-closed
// grounding check, same as any other real Contract Chat answer - an
// ungrounded "redline this contract" question with no contract in scope
// legitimately gets rejected before a model even answers, which would
// never produce a second badge to assert on.
//
// Requires the real local dev stack (docker compose up) with GOOGLE_API_KEY
// and ANTHROPIC_API_KEY configured for the backend - routing_service.py's
// STUDENT_MODEL_ID (gemini-2.5-flash) and TEACHER_MODEL_ID (claude-sonnet-5).

const CLEAN_MSA_PDF = path.resolve(__dirname, '../../data/Clean_MSA.pdf');

test.describe.configure({ retries: 2 });

async function registerAndSignIn(page: import('@playwright/test').Page) {
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  const username = `e2e_routing_${suffix}`;
  const tenantId = `e2e_routing_tenant_${suffix}`;
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
  // Real (non-mocked) success text - the enhanced-embeddings upload is
  // synchronous, so this text appearing means the contract is already
  // fully indexed and searchable, no extra polling needed.
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

async function sendPrompt(page: import('@playwright/test').Page, prompt: string) {
  await page.getByPlaceholder('Type your prompt here!').fill(prompt);
  await page.getByRole('button', { name: /Send your prompt now/ }).click();
}

async function waitForGenerationToFinish(page: import('@playwright/test').Page, timeoutMs = 120000) {
  await expect(page.locator('button:has-text("Stop generating")')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('button:has-text("Stop generating")')).toHaveCount(0, { timeout: timeoutMs });
}

test.describe('Autonomous student/teacher chat routing (Phase 6)', () => {
  test('a simple extraction prompt routes to the Student model and a redline/synthesis prompt routes to the Teacher model', async ({ page }) => {
    test.setTimeout(300000);

    await registerAndSignIn(page);
    await uploadRealContract(page);

    await page.getByRole('button', { name: 'Contract Chat' }).click();
    await setScope(page, 'Clean_MSA.pdf');

    await page.getByRole('combobox', { name: 'Model' }).click();
    await page.getByRole('option', { name: /Autonomous Routing/ }).click();

    // Every badge on the page, in DOM order - asserting on *count* first
    // (not just visibility) is what actually proves the second message's
    // own badge rendered, instead of racing ahead and re-inspecting the
    // first message's still-visible badge before the second reply lands.
    const badges = page.getByTestId('model-tier-badge');

    // Simple extraction -> Student.
    await sendPrompt(page, 'What is the payment term in this agreement?');
    await waitForGenerationToFinish(page);
    await expect(badges).toHaveCount(1, { timeout: 15000 });
    const studentBadge = badges.last();
    await expect(studentBadge).toHaveAttribute('data-tier', 'student');
    await expect(studentBadge).toContainText('Student Model');
    await page.screenshot({ path: 'test-results/phase6_student_routing.png', fullPage: true });

    // Redline/synthesis -> Teacher.
    await sendPrompt(
        page,
        'Please redline this agreement to cap our liability at 12 months of fees and synthesize the key risk points.',
    );
    await waitForGenerationToFinish(page);
    await expect(badges).toHaveCount(2, { timeout: 15000 });
    const teacherBadge = badges.last();
    await expect(teacherBadge).toHaveAttribute('data-tier', 'teacher');
    await expect(teacherBadge).toContainText('Teacher Model');
    await page.screenshot({ path: 'test-results/phase6_teacher_routing.png', fullPage: true });
  });
});
