import { test, expect } from '@playwright/test';

const MOCK_JWT = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0ZW5hbnRfaWQiOiJlMmVfdGVuYW50X3Rlc3QiLCJyb2xlIjoiQURNSU4iLCJleHAiOjIwMDAwMDAwMDB9.dummy_signature';

async function setupMockedAuthAndSignIn(page: import('@playwright/test').Page) {
  // Mock token API
  await page.route('**/api/auth/token*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: MOCK_JWT })
    });
  });

  // Mock registration API
  await page.route('**/api/auth/register*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'created', message: 'Account created' })
    });
  });

  // Mock workflow status API
  await page.route('**/api/workflow/status*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'idle', agent_executions: [] })
    });
  });

  // Mock models API
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

  // Mock contracts history API
  await page.route('**/api/contracts*', async (route) => {
    if (route.request().url().includes('/search')) {
      return route.fallback();
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([])
    });
  });

  await page.goto('/');

  // Sign in directly using mocked token endpoint
  await page.locator('#username').fill('admin');
  await page.locator('#password').fill('Password123!');
  await page.getByRole('button', { name: 'Sign in' }).click();

  // Wait for main navbar button
  await expect(page.getByRole('button', { name: 'Document Analysis' })).toBeVisible({ timeout: 15000 });
}

test.describe('Enhanced Search & Multi-Level Indexing E2E Verification', () => {

  test('Verification 1: Multi-Level Embeddings toggle is visible and functional in DocumentUpload UI', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Click Document Analysis tab to ensure DocumentUpload component is visible
    await page.getByRole('button', { name: 'Document Analysis' }).click();

    // Verify DocumentUpload container and toggle label
    const toggleLabel = page.getByText('Multi-Level Embeddings', { exact: true });
    await expect(toggleLabel).toBeVisible({ timeout: 10000 });

    const toggleDesc = page.getByText('Generate document, section, clause & relationship embeddings');
    await expect(toggleDesc).toBeVisible();

    const toggleInput = page.locator('#enhanced-upload-toggle');
    await expect(toggleInput).toBeVisible();

    // Checked by default
    await expect(toggleInput).toBeChecked();

    // Toggle off and verify state change
    await toggleInput.uncheck();
    await expect(toggleInput).not.toBeChecked();

    // Toggle back on and verify state change
    await toggleInput.check();
    await expect(toggleInput).toBeChecked();

    // Intercept upload API to mock enhanced response (with wildcard query params)
    await page.route('**/api/documents/enhanced/upload*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          filename: 'test_contract.pdf',
          contract_id: 'contract_e2e_123',
          enhanced_embeddings: true,
          details: 'Multi-level embeddings generated'
        })
      });
    });

    // Upload file with toggle active
    const buffer = Buffer.from('%PDF-1.4 mock pdf content for testing');
    await page.locator('input[type="file"]').setInputFiles({
      name: 'test_contract.pdf',
      mimeType: 'application/pdf',
      buffer: buffer
    });

    // Verify success status message in DOM with active multi-level embeddings tag
    await expect(page.getByText(/Contract created successfully/)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Multi-level Embeddings Active/)).toBeVisible();
    await expect(page.getByText(/contract_e2e_123/).first()).toBeVisible();
  });

  test('Verification 2: All Levels search correctly renders non-empty results on screen (plural keys fix)', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Navigate to Search tab
    await page.getByRole('button', { name: 'Enhanced Search' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Enhanced Contract Search' })).toBeVisible({ timeout: 10000 });

    // Select "All Levels" search level
    await page.getByText('All Levels', { exact: true }).click();

    // Intercept backend search API to return multi-level results with plural keys
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          search_level: 'all',
          contracts_found: 4,
          results: [
            {
              documents: [
                {
                  file_id: 'DOC-001-ALPHA',
                  summary: 'Master Services Agreement for cloud infrastructure services',
                  contract_type: 'MSA',
                  effective_date: '2025-01-01',
                  end_date: '2027-12-31',
                  parties: [{ name: 'Acme Corp', role: 'Client' }, { name: 'CloudServices LLC', role: 'Provider' }]
                }
              ],
              sections: [
                {
                  contract_id: 'DOC-001-ALPHA',
                  section_type: 'Termination',
                  content: 'Either party may terminate this agreement with 30 days written notice.',
                  order: 1
                }
              ],
              clauses: [
                {
                  contract_id: 'DOC-001-ALPHA',
                  clause_type: 'Indemnity',
                  content: 'Provider shall indemnify and hold harmless Client against third-party claims.',
                  confidence: 0.95,
                  start_position: 120,
                  end_position: 250
                }
              ],
              relationships: [
                {
                  contract_id: 'DOC-001-ALPHA',
                  party_name: 'Acme Corp',
                  role: 'Client',
                  context: 'Primary counterparty under MSA contract DOC-001-ALPHA'
                }
              ]
            }
          ],
          metadata: { search_level: 'all', query: 'indemnity services' }
        })
      });
    });

    // Fill query and execute search
    await page.getByPlaceholder('Enter your search query...').fill('indemnity services');
    await page.getByRole('button', { name: 'Search Contracts' }).click();

    // Verify all 4 level result section headings render on screen
    await expect(page.getByText('Document Results (1)')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Section Results (1)')).toBeVisible();
    await expect(page.getByText('Clause Results (1)')).toBeVisible();
    await expect(page.getByText('Relationship Results (1)')).toBeVisible();

    // Verify specific content rendered in DOM for all 4 levels
    await expect(page.getByText('DOC-001-ALPHA').first()).toBeVisible();
    await expect(page.getByText('Master Services Agreement for cloud infrastructure services')).toBeVisible();
    await expect(page.getByText('Provider shall indemnify and hold harmless Client against third-party claims.')).toBeVisible();
    await expect(page.getByText('Primary counterparty under MSA contract DOC-001-ALPHA')).toBeVisible();
  });

  test('Verification 3: Backend search errors are surfaced distinctly from no results found in UI', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Navigate to Search tab
    await page.getByRole('button', { name: 'Enhanced Search' }).click();

    // 1. Intercept search API with backend error response
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: false,
          search_level: 'clause',
          results: [],
          contracts_found: 0,
          error: 'Vector database index missing or unavailable',
          message: 'Search error occurred: Vector database index missing or unavailable',
          metadata: { search_level: 'clause', error: 'Vector database index missing or unavailable' }
        })
      });
    });

    await page.getByPlaceholder('Enter your search query...').fill('error test query');
    await page.getByRole('button', { name: 'Search Contracts' }).click();

    // Verify distinct red alert for backend error
    const errorAlert = page.locator('[role="alert"]');
    await expect(errorAlert).toBeVisible({ timeout: 10000 });
    await expect(errorAlert).toContainText('Vector database index missing or unavailable');

    // Verify generic "No results found" message is NOT displayed during an error
    await expect(page.getByText('No results found. Try adjusting your search criteria.')).toHaveCount(0);

    // 2. Now test a legitimate zero-results search
    await page.unroute('**/api/contracts/search/enhanced*');
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          search_level: 'clause',
          results: [],
          contracts_found: 0,
          metadata: { search_level: 'clause' }
        })
      });
    });

    await page.getByPlaceholder('Enter your search query...').fill('nonexistent query');
    await page.getByRole('button', { name: 'Search Contracts' }).click();

    // Verify legitimate empty search displays "No results found" and NOT error alert
    await expect(page.getByText('No results found. Try adjusting your search criteria.')).toBeVisible({ timeout: 10000 });
  });

  test('Verification 4: Filters (Contract Type, Active status) are actively applied in All Levels search payload', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Navigate to Search tab
    await page.getByRole('button', { name: 'Enhanced Search' }).click();

    // Select "All Levels" search mode
    await page.getByText('All Levels', { exact: true }).click();

    // Fill contract type filter
    await page.getByPlaceholder('e.g., MSA, SOW, NDA').fill('MSA');

    // Select Active Only
    await page.locator('select').selectOption('true');

    let capturedPayload: any = null;
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      const request = route.request();
      capturedPayload = request.postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          search_level: 'all',
          contracts_found: 1,
          results: [
            {
              documents: [
                {
                  file_id: 'DOC-MSA-ACTIVE',
                  summary: 'Filtered active MSA document',
                  contract_type: 'MSA',
                  effective_date: '2025-01-01',
                  end_date: '2027-12-31',
                  parties: [{ name: 'Acme Corp', role: 'Client' }]
                }
              ]
            }
          ],
          metadata: { search_level: 'all', contract_type: 'MSA', active: true }
        })
      });
    });

    await page.getByPlaceholder('Enter your search query...').fill('cloud hosting');
    await page.getByRole('button', { name: 'Search Contracts' }).click();

    // Verify request payload contained active and contract_type filters
    await expect.poll(() => capturedPayload).not.toBeNull();
    expect(capturedPayload.search_level).toBe('all');
    expect(capturedPayload.contract_type).toBe('MSA');
    expect(capturedPayload.active).toBe(true);

    // Verify filtered results rendered in DOM
    await expect(page.getByText('DOC-MSA-ACTIVE')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('Filtered active MSA document')).toBeVisible();
  });

  test('Verification 5: Empty search state displays clean user-friendly card without debug text', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Navigate to Search tab
    await page.getByRole('button', { name: 'Enhanced Search' }).click();

    // Intercept search API with empty array results
    await page.route('**/api/contracts/search/enhanced*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          search_level: 'document',
          contracts_found: 0,
          results: [{ documents: [] }],
          metadata: { search_level: 'document' }
        })
      });
    });

    await page.getByPlaceholder('Enter your search query...').fill('empty query test');
    await page.getByRole('button', { name: 'Search Contracts' }).click();

    // Verify clean "No results found" container is displayed
    const emptyNotice = page.getByText('No results found. Try adjusting your search criteria.');
    await expect(emptyNotice).toBeVisible({ timeout: 10000 });

    // Verify raw debug text ("Debug: Received object") is NOT present anywhere in DOM
    const bodyText = await page.content();
    expect(bodyText).not.toContain('Debug: Received object');
    expect(bodyText).not.toContain('Debug:');
  });

});
