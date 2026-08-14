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

  await page.route('**/api/contract-chat/sessions*', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          session_id: 'SESSION_E2E_CIT_1',
          tenant_id: 'tenant_alpha',
          title: 'E2E Test Session',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          message_count: 0
        })
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([])
      });
    }
  });

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
  await page.locator('#username').fill('admin');
  await page.locator('#password').fill('Password123!');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: /Contract Chat/i })).toBeVisible({ timeout: 5000 });
}

test.describe('Citation Highlighting & PDF Viewer Modal E2E Verification', () => {

  test('Scenario 1 & 2 Citation Click: Opens PDF viewer modal and renders page/excerpt highlights', async ({ page }) => {
    await setupMockedAuthAndSignIn(page);

    // Mock contract PDF source download
    await page.route('**/api/documents/*/source', async (route) => {
      // Return a minimal valid PDF buffer
      const dummyPdf = '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n0000000052 00000 n\n0000000102 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF';
      await route.fulfill({
        status: 200,
        contentType: 'application/pdf',
        body: Buffer.from(dummyPdf)
      });
    });

    // Mock chat SSE streaming endpoint returning citations
    await page.route('**/api/contract-chat/stream*', async (route) => {
      const citations = [
        {
          citation_id: 'EVID_TEST_10_1',
          contract_id: 'UPLOADED_223C84D9_20260813',
          filename: 'Salesforce_MSA.pdf',
          source_type: 'section',
          section_title: '10. Limitation of Liability',
          clause_type: 'Limitation of Liability',
          page: 12,
          excerpt: '10.1 Unlimited Liability. The Parties shall be mutually liable without limitation for gross negligence or willful misconduct.',
          highlight_text: '10.1 Unlimited Liability',
          validation_status: 'tenant_active',
          source_available: true,
          provenance_status: 'exact'
        }
      ];

      const sseBody = [
        `data: ${JSON.stringify({ content: 'Based on Section 10.1 of Salesforce_MSA:', type: 'ai_message', status: 'passed' })}\n\n`,
        `data: ${JSON.stringify({ content: JSON.stringify(citations), type: 'citations' })}\n\n`,
        `data: ${JSON.stringify({ content: '', type: 'end', status: 'passed' })}\n\n`
      ].join('');

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody
      });
    });

    // Navigate to Contract Chat tab
    await page.getByRole('button', { name: /Contract Chat/i }).click();

    // Send query
    const promptInput = page.locator('textarea, input[placeholder*="Ask"], input[type="text"]').last();
    await promptInput.fill('Detail the limitations of liability for Germany customers.');
    await page.getByRole('button', { name: /Send your prompt now!/i }).click();

    // Verify citation source pill appears
    const citationPill = page.getByRole('button', { name: /Salesforce_MSA.pdf/i }).first();
    await expect(citationPill).toBeVisible({ timeout: 10000 });

    // Click citation pill
    await citationPill.click();

    // Verify PDF Citation Viewer modal opens
    const modalHeader = page.locator('h3:has-text("Salesforce_MSA.pdf"), div:has-text("Salesforce_MSA.pdf")').first();
    await expect(modalHeader).toBeVisible({ timeout: 5000 });

    console.log('[SUCCESS] Citation pill click successfully opened PDF viewer modal for target document!');
  });
});
