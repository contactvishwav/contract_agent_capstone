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

test.describe('Phase 1: Document & Section Tabs UI and Functional Verification', () => {

  test('Test 1: The "Document" Tab - Fields, Network Payload, & Document Results Only', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Click Enhanced Search tab
    await page.getByRole('button', { name: /Enhanced Search/i }).click();

    // Select Document Level tab radio input
    await page.locator('label:has-text("Document")').first().click();

    // Fill Query ("termination")
    await page.locator('#search-query').fill('termination');
    
    // Fill Contract Type ("MSA")
    const contractTypeInput = page.locator('input[placeholder*="MSA, SOW"]');
    await contractTypeInput.fill('MSA');

    // Fill Party ("Acme Corp")
    const partyInput = page.locator('input[placeholder*="Acme Corp"]');
    await partyInput.fill('Acme Corp');

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
              documents: [
                {
                  file_id: 'CONTRACT_DOC_1',
                  filename: 'Clean_MSA.pdf',
                  summary: 'Clean Master Services Agreement with standard termination clauses.',
                  contract_type: 'MSA',
                  effective_date: '2025-01-01',
                  end_date: '2026-01-01',
                  parties: [{ name: 'Acme Corp', role: 'Client' }, { name: 'ConsultCorp', role: 'Vendor' }]
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
    expect(capturedPayload.search_level).toBe('document');
    expect(capturedPayload.query).toBe('termination');
    expect(capturedPayload.contract_type).toBe('MSA');
    expect(capturedPayload.parties).toEqual(['Acme Corp']);

    console.log('[PAYLOAD VERIFIED - TEST 1]', JSON.stringify(capturedPayload));

    // Verify UI renders ONLY Document Results card
    await expect(page.locator('h3:has-text("Document Results")')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('h3:has-text("Section Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Clause Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Relationship Results")')).not.toBeVisible();
    await expect(page.locator('h4:has-text("Clean_MSA.pdf")')).toBeVisible();

    console.log('[UI VERIFIED - TEST 1] Document Results card rendered exclusively.');
  });

  test('Test 2: The "Section" Tab & Checkbox Array Controls + Filtered Search', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Click Enhanced Search tab
    await page.getByRole('button', { name: /Enhanced Search/i }).click();

    // Click "Section" tab radio input
    await page.locator('label:has-text("Section")').first().click();

    // Verify "Section Types" checkbox array renders
    const sectionTypeLabel = page.locator('label:has-text("Section Types")');
    await expect(sectionTypeLabel).toBeVisible();

    // Click Payment Terms and Liability checkboxes
    await page.locator('span:has-text("Payment Terms")').click();
    await page.locator('span:has-text("Liability")').click();

    // Verify counter shows "(2 selected)"
    await expect(page.locator('text=Section Types (2 selected)')).toBeVisible();

    // Test All bulk button
    await page.getByRole('button', { name: 'All' }).click();
    await expect(page.locator('text=Section Types (6 selected)')).toBeVisible();

    // Test None bulk button
    await page.getByRole('button', { name: 'None' }).click();
    await expect(page.locator('text=Section Types (0 selected)')).toBeVisible();

    // Re-check Payment Terms
    await page.locator('span:has-text("Payment Terms")').click();
    await expect(page.locator('text=Section Types (1 selected)')).toBeVisible();

    // Enter Search Query "payment"
    await page.locator('#search-query').fill('payment');

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
              sections: [
                {
                  contract_id: 'CONTRACT_SEC_1',
                  filename: 'Clean_MSA.pdf',
                  section_type: 'payment',
                  content: '2. Payment Terms: Client shall pay invoices within 90 days of receipt.',
                  order: 2
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
    expect(capturedPayload.search_level).toBe('section');
    expect(capturedPayload.query).toBe('payment');
    expect(capturedPayload.section_types).toEqual(['payment']);

    console.log('[PAYLOAD VERIFIED - TEST 2]', JSON.stringify(capturedPayload));

    // Verify UI renders ONLY Section Results card
    await expect(page.locator('h3:has-text("Section Results")')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('h3:has-text("Document Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Clause Results")')).not.toBeVisible();
    await expect(page.locator('h3:has-text("Relationship Results")')).not.toBeVisible();
    await expect(page.getByText('payment', { exact: true })).toBeVisible();

    console.log('[UI VERIFIED - TEST 2] Section Results card rendered exclusively with payment section_type filter.');
  });
});
