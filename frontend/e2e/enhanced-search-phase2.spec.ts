import { test, expect } from '@playwright/test';

const MOCK_JWT = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0ZW5hbnRfaWQiOiJ0ZW5hbnRfYWxwaGEiLCJyb2xlIjoiQURNSU4iLCJleHAiOjIwMDAwMDAwMDB9.dummy_signature';

async function setupMockedAuthAndSignIn(page: import('@playwright/test').Page) {
  await page.route('**/api/auth/token*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: MOCK_JWT })
    });
  });

  await page.route('**/api/auth/register*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'created', message: 'Account created' })
    });
  });

  await page.route('**/api/workflow/status*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'idle', agent_executions: [] })
    });
  });

  await page.route('**/api/models*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        workflow: 'analysis',
        models: [
          {
            id: 'gemini-2.5-flash',
            provider: 'google',
            display_label: 'Gemini 2.5 Flash',
            configured: true,
            capabilities: [],
            production_allowed: true,
            fallback_eligible: false,
            cost_class: 'low',
            latency_class: 'fast',
            deprecated: false
          }
        ],
        default_model: 'gemini-2.5-flash',
        embedding: { provider: 'google', model: 'text-embedding-004', dimensions: 768, user_selectable: false, reason: '' },
        fallback_policy: { automatic_cross_provider: false, disclosure_required: true, legal_analysis: '' }
      })
    });
  });

  await page.route('**/api/contracts', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([])
      });
    } else {
      await route.fallback();
    }
  });

  await page.goto('/');
  await page.locator('#username').fill('admin');
  await page.locator('#password').fill('Password123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: /Enhanced Search/i })).toBeVisible({ timeout: 5000 });
}

test.describe('Phase 2: Clause & Relationship Tabs UI and Functional Verification', () => {

  test('Test 3: The "Clause" Tab & CUAD Clause Types Dropdown + Filtered Search', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Click Enhanced Search tab
    await page.getByRole('button', { name: /Enhanced Search/i }).click();

    // Select Clause Level tab radio input
    await page.locator('label:has-text("Clause")').first().click();

    // Verify CUAD Clause Types control renders, Section Types is hidden
    await expect(page.locator('label:has-text("CUAD Clause Types")')).toBeVisible();
    await expect(page.locator('label:has-text("Section Types")')).not.toBeVisible();

    // Click "Show" to expand dropdown
    await page.getByRole('button', { name: /Show/i }).click();

    // Select "Termination For Convenience" and "Non-Compete" checkboxes
    await page.locator('span:has-text("Termination For Convenience")').click();
    await page.locator('span:has-text("Non-Compete")').click();

    // Verify counter shows "(2 selected)"
    await expect(page.locator('text=CUAD Clause Types (2 selected)')).toBeVisible();

    // Enter Search Query ("termination notice")
    await page.locator('#search-query').fill('termination notice');

    // Fill Contract Type ("MSA")
    const contractTypeInput = page.locator('input[placeholder*="MSA, SOW"]');
    await contractTypeInput.fill('MSA');

    // Intercept network payload for /api/contracts/search/enhanced
    let capturedPayload: any = null;
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      if (route.request().method() === 'POST') {
        capturedPayload = JSON.parse(route.request().postData() || '{}');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            {
              clauses: [
                {
                  contract_id: 'CONTRACT_CLAUSE_1',
                  filename: 'Clean_MSA.pdf',
                  clause_type: 'Termination For Convenience',
                  content: '6. Termination for Convenience: Client may terminate this Agreement at any time upon 30 days notice.',
                  confidence: 0.95,
                  start_position: 120,
                  end_position: 240
                }
              ]
            }
          ])
        });
      } else {
        await route.fallback();
      }
    });

    // Click Search Contracts button
    await page.getByRole('button', { name: /Search Contracts/i }).click();

    // Verify network payload fields
    expect(capturedPayload).not.toBeNull();
    expect(capturedPayload.search_level).toBe('clause');
    expect(capturedPayload.query).toBe('termination notice');
    expect(capturedPayload.contract_type).toBe('MSA');
    expect(capturedPayload.clause_types).toEqual(['Termination For Convenience', 'Non-Compete']);

    console.log('[PAYLOAD VERIFIED - TEST 3]', JSON.stringify(capturedPayload));

    // Verify UI renders ONLY Clause Results card
    await expect(page.locator('h3:has-text("Clause Results")')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('h3:has-text("Document Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Section Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Relationship Results")')).not.toBeVisible();
    await expect(page.locator('span.bg-purple-100:has-text("Termination For Convenience")')).toBeVisible();

    console.log('[UI VERIFIED - TEST 3] Clause Results card rendered exclusively with CUAD clause_types filter.');
  });

  test('Test 4: The "Relationship" Tab & Party Filters + Blank Query Search', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Click Enhanced Search tab
    await page.getByRole('button', { name: /Enhanced Search/i }).click();

    // Click "Relationship" tab radio input
    await page.locator('label:has-text("Relationship")').first().click();

    // Verify both CUAD Clause Types and Section Types controls are hidden
    await expect(page.locator('label:has-text("CUAD Clause Types")')).not.toBeVisible();
    await expect(page.locator('label:has-text("Section Types")')).not.toBeVisible();

    // Fill Parties ("ConsultCorp, ClientCo") and leave Search Query blank
    const partyInput = page.locator('input[placeholder*="Acme Corp"]');
    await partyInput.fill('ConsultCorp, ClientCo');

    // Intercept network payload for /api/contracts/search/enhanced
    let capturedPayload: any = null;
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      if (route.request().method() === 'POST') {
        capturedPayload = JSON.parse(route.request().postData() || '{}');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([
            {
              relationships: [
                {
                  contract_id: 'CONTRACT_REL_1',
                  filename: 'Clean_MSA.pdf',
                  party_name: 'ConsultCorp',
                  role: 'Vendor',
                  context: 'ConsultCorp provides consulting services to ClientCo under Clean_MSA.'
                }
              ]
            }
          ])
        });
      } else {
        await route.fallback();
      }
    });

    // Click Search Contracts button
    await page.getByRole('button', { name: /Search Contracts/i }).click();

    // Verify network payload fields
    expect(capturedPayload).not.toBeNull();
    expect(capturedPayload.search_level).toBe('relationship');
    expect(capturedPayload.query).toBeNull();
    expect(capturedPayload.parties).toEqual(['ConsultCorp', 'ClientCo']);

    console.log('[PAYLOAD VERIFIED - TEST 4]', JSON.stringify(capturedPayload));

    // Verify UI renders ONLY Relationship Results card
    await expect(page.locator('h3:has-text("Relationship Results")')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('h3:has-text("Document Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Section Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Clause Results")')).not.toBeVisible();
    await expect(page.locator('span:has-text("ConsultCorp")').first()).toBeVisible();

    console.log('[UI VERIFIED - TEST 4] Relationship Results card rendered exclusively for party relationship graphs.');
  });
});
