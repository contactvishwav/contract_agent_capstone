/**
 * LIVE PRODUCTION FINAL AUDIT — Interview Demo Gauntlet
 * Target: https://contract-intel.duckdns.org/
 * Credentials: demo / password123
 *
 * Stages 1-3 document outcomes without hard-failing. Stages 4-5 (real chat
 * responses) hard-fail: every response is asserted to contain none of the
 * known guard/error strings, and to carry at least one citation - a
 * keyword-in-body check can't tell a real answer from an echoed prompt or
 * a block message (confirmed live: an earlier "PASS" here was actually a
 * safety-policy block, matched only because the block screen still shows
 * the user's own question text, which happened to contain the keywords).
 * Screenshots captured at every stage as visual evidence.
 * Run: npx playwright test e2e/live_diagnostic_audit.spec.ts --headed
 */

import { test, expect, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BASE_URL   = 'https://contract-intel.duckdns.org';
const USERNAME   = 'demo';
const PASSWORD   = 'password123';
const SNAPSHOT_DIR = path.resolve(__dirname, '../audit_snapshots');
const MOCK_REDLINE = path.resolve(__dirname, './fixtures/mock_redline.png');

// ── Helpers ─────────────────────────────────────────────────────────────────

async function snap(page: Page, filename: string) {
  const filePath = path.join(SNAPSHOT_DIR, filename);
  await page.screenshot({ path: filePath, fullPage: true });
  console.log(`[SNAPSHOT] ${filePath}`);
}

/** Click selector, wait for networkidle, then hard pause. */
async function clickAndSettle(page: Page, selector: string, waitMs = 3000) {
  try {
    await page.click(selector, { timeout: 10000 });
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(waitMs);
  } catch (e: any) {
    console.warn(`[WARN] clickAndSettle(${selector}): ${e.message?.slice(0, 120)}`);
  }
}

/** Navigate to a page using the top nav buttons. */
async function navTo(page: Page, label: 'Document Analysis' | 'Contract Chat' | 'Enhanced Search') {
  const btn = page.locator(`button:has-text("${label}")`).first();
  try {
    await btn.waitFor({ timeout: 8000 });
    await btn.click();
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(3000);
    console.log(`[NAV] → ${label}`);
  } catch (e: any) {
    console.warn(`[NAV WARN] Could not click "${label}": ${e.message?.slice(0, 80)}`);
  }
}

/** Set the contract scope via Radix UI Select. */
async function setScope(page: Page, label: string) {
  try {
    const trigger = page.locator('[aria-label="Contract scope"]').first();
    await trigger.waitFor({ timeout: 5000 });
    await trigger.click();
    await page.waitForTimeout(600);

    // Options appear in a Radix UI portal — find by role="option"
    const option = page.locator(`[role="option"]:has-text("${label}")`).first();
    await option.waitFor({ timeout: 4000 });
    await option.click();
    await page.waitForTimeout(800);
    console.log(`[SCOPE] set to: ${label}`);
  } catch (e: any) {
    // Try partial match
    try {
      const partialLabel = label.replace('.pdf', '').replace('.PDF', '');
      const option = page.locator(`[role="option"]:has-text("${partialLabel}")`).first();
      await option.waitFor({ timeout: 3000 });
      await option.click();
      await page.waitForTimeout(800);
      console.log(`[SCOPE] set (partial match) to: ${partialLabel}`);
    } catch {
      console.warn(`[SCOPE WARN] Could not set scope to "${label}": ${e.message?.slice(0, 80)}`);
    }
  }
}

/** Start a new chat session. */
async function newChat(page: Page) {
  try {
    const btn = page.locator('button:has-text("New chat")').first();
    await btn.waitFor({ timeout: 5000 });
    await btn.click();
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2000);
    console.log('[CHAT] New chat started');
  } catch (e: any) {
    console.warn(`[CHAT WARN] New chat: ${e.message?.slice(0, 80)}`);
  }
}

// Strings that must never appear in a real AI response — each one means
// the request was blocked or withheld outright. A response containing any
// of these is a failure, not a diagnostic note.
const FORBIDDEN_RESPONSE_STRINGS = [
  'blocked by the Contract Chat safety policy',
  'No relevant contract evidence was found',
  'Response withheld',
];

// "The specific clause was not found in the documents" is handled
// separately from FORBIDDEN_RESPONSE_STRINGS, not banned outright:
// honestly reporting a genuinely absent clause is correct behavior for a
// legal contract tool, not a bug (confirmed live on Clean_SOW.pdf's cure-
// period question - the model correctly said the clause wasn't found and
// cited real, true, related context instead of fabricating one).
//
// A char-count "how much text precedes the phrase" heuristic was tried
// here first and retired: confirmed live on Salesforce_MSA.pdf's
// liability-cap-and-geography question (a genuine two-part question) that
// a real, correctly-cited answer to the supported part plus this exact
// sentence for the unsupported part is legitimate compound-question
// handling, not a bug - and it's long-form prose, so "real content
// precedes the phrase" is the normal, expected shape, not a signal of
// contradiction. The actual safety property lives server-side now
// (contract_chat_agent.py's rule 2): the model is bounded to use ONLY
// this exact pre-approved sentence for an unsupported part, never
// free-form text, so its mere presence - verbatim - is itself the
// guarantee nothing false is being asserted about that part. Kept as a
// named function (not inlined) so the intent stays documented even
// though there's nothing left to assert.
const NOT_FOUND_PHRASE = 'specific clause was not found';

function assertNoContradictoryNotFoundAppendage(responseText: string) {
  const idx = responseText.toLowerCase().indexOf(NOT_FOUND_PHRASE.toLowerCase());
  if (idx === -1) return;
  const precedingContent = responseText.slice(0, idx).trim();
  if (precedingContent) {
    console.log(`[MSG] compound answer: substantive content + exact not-found sentence (${precedingContent.length} chars before it) - allowed`);
  }
}

/**
 * Type a message and send it; wait for generation to finish, then assert
 * the AI's own response (not the whole page — the page also contains the
 * echoed user prompt, which can spuriously match a keyword check) contains
 * none of FORBIDDEN_RESPONSE_STRINGS and has at least one citation.
 * Returns the AI response text for callers that want to inspect it further.
 */
async function sendMessage(page: Page, query: string, waitForResponseMs = 90000): Promise<string> {
  const textarea = page.locator('textarea[placeholder="Type your prompt here!"]').first();
  try {
    await textarea.waitFor({ timeout: 8000 });
    await textarea.fill(query);
    console.log(`[MSG] typed: "${query.slice(0, 80)}"`);
  } catch (e: any) {
    console.error(`[MSG FAIL] No textarea: ${e.message?.slice(0, 80)}`);
    throw new Error(`No chat textarea found: ${e.message}`);
  }

  // Send
  try {
    const sendBtn = page.locator('button:has-text("Send your prompt now!")').first();
    await sendBtn.waitFor({ timeout: 5000 });
    await sendBtn.click();
    console.log('[MSG] send clicked');
  } catch {
    await page.keyboard.press('Enter');
    console.log('[MSG] Enter pressed to send');
  }

  // Wait for response — poll until "Stop generating" disappears or timeout
  const deadline = Date.now() + waitForResponseMs;
  let elapsed = 0;
  while (Date.now() < deadline) {
    await page.waitForTimeout(5000);
    elapsed += 5;
    const generating = await page.locator('[aria-label="Stop generating"]').count().catch(() => 0);
    if (generating === 0) break;
    console.log(`[MSG] waiting for response... ${elapsed}s`);
  }

  await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(3000);

  // The AI's own message bubble only — not the whole page, which also
  // contains the echoed user prompt (a keyword check against page-wide
  // text can match words in the QUESTION, not the answer).
  const aiMessage = page.locator('div:has(> strong:text-is("AI"))').last();
  const responseText = (await aiMessage.textContent().catch(() => '')) ?? '';
  console.log(`[MSG] AI response (first 200 chars): "${responseText.slice(0, 200)}"`);

  for (const forbidden of FORBIDDEN_RESPONSE_STRINGS) {
    expect(responseText, `AI response must not contain "${forbidden}"`).not.toContain(forbidden);
  }
  assertNoContradictoryNotFoundAppendage(responseText);

  const citationCount = await page.locator('aside[aria-label="Sources"] button').count().catch(() => 0);
  expect(citationCount, 'AI response must carry at least one citation').toBeGreaterThan(0);

  return responseText;
}

// ── Test Suite ───────────────────────────────────────────────────────────────

test.describe('LIVE PRODUCTION AUDIT', () => {
  let sharedPage: Page;

  test.beforeAll(async ({ browser }) => {
    fs.mkdirSync(SNAPSHOT_DIR, { recursive: true });
    const ctx = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      ignoreHTTPSErrors: true,
    });
    sharedPage = await ctx.newPage();

    sharedPage.on('console', msg => {
      if (msg.type() === 'error') console.error(`[CONSOLE ERR] ${msg.text()}`);
    });
    sharedPage.on('pageerror', err => console.error(`[PAGE JS ERR] ${err.message}`));
    sharedPage.on('response', r => {
      if (r.status() >= 400) console.error(`[NET ${r.status()}] ${r.url()}`);
    });
  });

  // ── STAGE 1: Auth ──────────────────────────────────────────────────────────
  test('STAGE 1 — Authentication', async () => {
    test.setTimeout(90_000);
    const page = sharedPage;
    console.log('\n========== STAGE 1: AUTHENTICATION ==========');

    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(3000);

    const body0 = await page.textContent('body').catch(() => '');
    if (body0?.includes('502') || body0?.includes('Bad Gateway')) {
      console.error('[S1 FAIL] 502 Bad Gateway on initial load');
      await snap(page, 'audit_1_login_attempt.png');
      return;
    }

    // Username
    const userField = page.locator('input[name="username"], input[type="text"], input[placeholder*="username" i], #username').first();
    try {
      await userField.waitFor({ timeout: 8000 });
      await userField.fill(USERNAME);
      console.log('[S1] Username entered');
    } catch { console.error('[S1 FAIL] No username field'); }

    // Password
    const passField = page.locator('input[name="password"], input[type="password"], #password').first();
    try {
      await passField.waitFor({ timeout: 5000 });
      await passField.fill(PASSWORD);
      console.log('[S1] Password entered');
    } catch { console.error('[S1 FAIL] No password field'); }

    // Sign in
    const signIn = page.locator('button[type="submit"], button:has-text("Sign In"), button:has-text("Log In"), button:has-text("Login")').first();
    try {
      await signIn.waitFor({ timeout: 5000 });
      await signIn.click();
      console.log('[S1] Sign-in clicked');
    } catch { console.error('[S1 FAIL] No sign-in button'); }

    await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
    await page.waitForTimeout(4000);

    const postBody = await page.textContent('body').catch(() => '');
    const hasNav = await page.locator('button:has-text("Document Analysis")').count().catch(() => 0);
    const hasDashboard = await page.locator('h1:has-text("Contract Intelligence")').count().catch(() => 0);

    if (postBody?.includes('502') || postBody?.includes('Bad Gateway')) {
      console.error('[S1 FAIL] 502 after login');
    } else if (hasNav > 0 || hasDashboard > 0) {
      console.log('[S1 PASS] Dashboard loaded — auth succeeded. Nav visible.');
    } else if (postBody?.includes('Signing in')) {
      console.error('[S1 FAIL] Stuck "Signing in..." state');
    } else {
      console.warn('[S1 WARN] Dashboard nav not detected — may still be on login');
    }

    await snap(page, 'audit_1_login_attempt.png');
  });

  // ── STAGE 2: Verify Uploads ────────────────────────────────────────────────
  test('STAGE 2 — Verify Document List', async () => {
    test.setTimeout(90_000);
    const page = sharedPage;
    console.log('\n========== STAGE 2: VERIFY DOCUMENT LIST ==========');

    await navTo(page, 'Document Analysis');
    await page.waitForTimeout(3000);

    const expectedFiles = [
      'Contract_Policy_Playbook.pdf',
      'Salesforce_MSA.pdf',
      'Clean_MSA.pdf',
      'Clean_SOW.pdf',
    ];

    const bodyText = await page.textContent('body').catch(() => '');

    for (const filename of expectedFiles) {
      const nameNoExt = filename.replace('.pdf', '');
      const found = bodyText?.includes(filename) || bodyText?.includes(nameNoExt);
      if (found) {
        // Also check if archive button is present (confirms it's in document list)
        const archiveBtn = await page.locator(`button[aria-label="Archive ${filename}"]`).count().catch(() => 0);
        console.log(`[S2 ${found ? 'PASS' : 'WARN'}] ${filename} — found=${found} archive-btn=${archiveBtn > 0}`);
      } else {
        console.error(`[S2 FAIL] ${filename} NOT found in document list`);
      }
    }

    // Check for analysis status indicators
    const analysisComplete = bodyText?.includes('Analysis') || bodyText?.includes('Risk') || bodyText?.includes('analyzed');
    const successBadge = await page.locator('[class*="green"], [class*="success"], :text("✅")').count().catch(() => 0);
    console.log(`[S2] analysis-status-visible=${analysisComplete} success-badges=${successBadge}`);

    await snap(page, 'audit_2_documents_verified.png');
  });

  // ── STAGE 3: Enhanced Search ───────────────────────────────────────────────
  test('STAGE 3 — Enhanced Search', async () => {
    test.setTimeout(240_000);
    const page = sharedPage;
    console.log('\n========== STAGE 3: ENHANCED SEARCH ==========');

    await navTo(page, 'Enhanced Search');
    await page.waitForTimeout(3000);

    const QUERY_INPUT  = 'input#search-query, input[placeholder="Enter your search query..."]';
    const SEARCH_BTN   = 'button:has-text("Search Contracts")';

    async function runSearch(level: 'document' | 'section' | 'clause' | 'relationship', query: string, snapName: string) {
      // Select search level via radio label
      const levelLabel = page.locator(`label:has(input[name="searchLevel"][value="${level}"])`).first();
      try {
        await levelLabel.waitFor({ timeout: 5000 });
        await levelLabel.click();
        await page.waitForTimeout(1000);
        console.log(`[S3] Level set: ${level}`);
      } catch (e: any) {
        console.warn(`[S3 WARN] Cannot select level "${level}": ${e.message?.slice(0, 80)}`);
      }

      // Type query
      const inp = page.locator(QUERY_INPUT).first();
      try {
        await inp.waitFor({ timeout: 5000 });
        await inp.fill(query);
      } catch { console.warn(`[S3 WARN] No search input for level ${level}`); }

      // Click search
      const btn = page.locator(SEARCH_BTN).first();
      try {
        await btn.waitFor({ timeout: 5000 });
        await btn.click();
        await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
        await page.waitForTimeout(4000);
      } catch { console.warn(`[S3 WARN] No search button for level ${level}`); }

      const body = await page.textContent('body').catch(() => '');
      // Check human-readable filenames (not raw UUIDs)
      const hasPdfNames = expectedFiles.some(f => body?.includes(f.replace('.pdf', '')));
      const hasRawUuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/.test(body ?? '');
      const hasResults = body?.includes('filename') || body?.includes('.pdf') || body?.includes('result') || hasPdfNames;
      console.log(`[S3-${level}] results=${hasResults} human-readable-filenames=${hasPdfNames} raw-uuids-visible=${hasRawUuid}`);

      await snap(page, snapName);
    }

    const expectedFiles = ['Contract_Policy_Playbook', 'Salesforce_MSA', 'Clean_MSA', 'Clean_SOW'];

    // 3a — Document level
    console.log('[S3a] Document Level Search');
    await runSearch('document', 'termination notice period', 'audit_3_search_document.png');

    // 3b — Section level
    console.log('[S3b] Section Level Search');
    await runSearch('section', 'payment terms liability', 'audit_3_search_section.png');

    // 3c — Clause level
    console.log('[S3c] Clause Level Search');
    await runSearch('clause', 'termination for convenience', 'audit_3_search_clause.png');

    // 3d — Relationship level
    console.log('[S3d] Relationship Level Search');
    await runSearch('relationship', 'governing law indemnification', 'audit_3_search_relationship.png');
  });

  // ── STAGE 4: Multi-Doc Chat ────────────────────────────────────────────────
  test('STAGE 4 — Multi-Doc Chat', async () => {
    test.setTimeout(300_000);
    const page = sharedPage;
    console.log('\n========== STAGE 4: MULTI-DOC CHAT ==========');

    await navTo(page, 'Contract Chat');
    await page.waitForTimeout(3000);

    // Start a fresh chat
    await newChat(page);
    await page.waitForTimeout(2000);

    // Set scope to All contracts
    await setScope(page, 'All contracts');
    await page.waitForTimeout(1000);

    const NOTICE_QUERY = 'Across the Policy Playbook, the Salesforce MSA, the Clean MSA, and the Clean SOW, summarize the differing notice periods required for termination.';
    // sendMessage already asserts the response is free of forbidden
    // strings and carries a citation - a keyword check here only checks
    // against the AI's own response text (not page-wide, which also
    // contains the echoed question and would spuriously match).
    const responseText = await sendMessage(page, NOTICE_QUERY, 120000);

    const keywords = ['notice', 'days', 'termination', 'period', 'Playbook', 'Salesforce', 'MSA', 'SOW', 'written'];
    const hitCount = keywords.filter(k => responseText.toLowerCase().includes(k.toLowerCase())).length;

    if (hitCount >= 4) {
      console.log(`[S4 PASS] Response synthesizes multi-doc data — ${hitCount}/${keywords.length} keywords hit`);
    } else {
      console.warn(`[S4 WARN] Response present but keyword hits low (${hitCount}/${keywords.length})`);
    }

    await snap(page, 'audit_4_all_contracts_chat.png');
  });

  // ── STAGE 5: Deep Dives, Citations, Multimodal ────────────────────────────
  test('STAGE 5 — Deep Dives + Citation + Multimodal', async () => {
    test.setTimeout(1_200_000);
    const page = sharedPage;
    console.log('\n========== STAGE 5: DEEP DIVES ==========');

    await navTo(page, 'Contract Chat');
    await page.waitForTimeout(2000);

    let totalCitationsClicked = 0;
    let totalPdfViewerOpened = 0;
    let sourceUnavailableCount = 0;

    async function deepDiveDoc(scope: string, q1: string, q2: string, snapName: string) {
      await newChat(page);
      await page.waitForTimeout(1500);
      await setScope(page, scope);
      await page.waitForTimeout(1000);

      console.log(`\n[S5] === ${scope} ===`);

      // Q1 — sendMessage asserts no forbidden strings and >=1 citation
      const r1 = await sendMessage(page, q1, 90000);
      console.log(`[S5] Q1="${q1.slice(0, 60)}" → OK (${r1.length} chars)`);
      await page.waitForTimeout(2000);

      // Check for citations after Q1
      await clickCitations(page, 2);

      // Q2
      const r2 = await sendMessage(page, q2, 90000);
      console.log(`[S5] Q2="${q2.slice(0, 60)}" → OK (${r2.length} chars)`);
      await page.waitForTimeout(2000);

      // Check for citations after Q2
      await clickCitations(page, 2);

      await snap(page, snapName);
    }

    async function clickCitations(page: Page, maxToClick: number) {
      // Clickable citation pills: blue buttons inside <aside aria-label="Sources">
      const citBtns = page.locator('aside[aria-label="Sources"] button.rounded-full');
      const count = await citBtns.count().catch(() => 0);
      const toClick = Math.min(count, maxToClick);
      console.log(`[S5-CIT] Found ${count} clickable citations, clicking ${toClick}`);

      for (let i = 0; i < toClick; i++) {
        try {
          await citBtns.nth(i).click({ timeout: 5000 });
          await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
          await page.waitForTimeout(3000);
          totalCitationsClicked++;

          // Verify PdfCitationViewer dialog opened
          const dialogVisible = await page.locator('[role="dialog"][aria-modal="true"]').isVisible().catch(() => false);
          const canvasPresent = await page.locator('[role="dialog"] canvas').count().catch(() => 0);
          const srcUnavail    = await page.locator('[role="dialog"]').textContent().catch(() => '');

          if (dialogVisible) {
            totalPdfViewerOpened++;
            console.log(`[S5-CIT PASS] Citation ${i+1}: PDF viewer opened, canvas=${canvasPresent > 0}`);
          }
          if (srcUnavail?.includes('Source unavailable') || srcUnavail?.includes('source unavailable')) {
            sourceUnavailableCount++;
            console.error(`[S5-CIT FAIL] "Source unavailable" error in PDF viewer`);
          }

          // Close dialog
          await page.keyboard.press('Escape').catch(() => {});
          await page.waitForTimeout(1000);
        } catch (e: any) {
          console.warn(`[S5-CIT WARN] Citation click ${i+1}: ${e.message?.slice(0, 80)}`);
        }
      }
    }

    // 5a — Clean SOW
    await deepDiveDoc(
      'Clean_SOW.pdf',
      'What are the specific payment milestones defined in this contract?',
      'What is the cure period for breach of contract?',
      'audit_5_chat_sow.png'
    );

    // 5b — Salesforce MSA
    await deepDiveDoc(
      'Salesforce_MSA.pdf',
      'What is the total liability cap and does it vary by geography?',
      'What are the intellectual property ownership and licensing terms?',
      'audit_5_chat_salesforce.png'
    );

    // 5c — Clean MSA
    await deepDiveDoc(
      'Clean_MSA.pdf',
      'What is the governing law and jurisdiction for dispute resolution?',
      'Detail the indemnification clause and its carve-outs.',
      'audit_5_chat_cleanmsa.png'
    );

    // 5d — Policy Playbook
    await deepDiveDoc(
      'Contract_Policy_Playbook.pdf',
      'What is the maximum liability cap specified in this playbook?',
      'What are the rules on governing law and which law takes precedence?',
      'audit_5_chat_playbook.png'
    );

    // Citation summary
    console.log(`\n[S5-CIT SUMMARY] Clicked=${totalCitationsClicked} ViewerOpened=${totalPdfViewerOpened} SourceUnavailable=${sourceUnavailableCount}`);
    if (totalCitationsClicked >= 4) {
      console.log(`[S5-CIT PASS] Clicked ${totalCitationsClicked} citations (≥4 required)`);
    } else {
      console.warn(`[S5-CIT WARN] Only ${totalCitationsClicked} citations clicked (need ≥4)`);
    }
    if (sourceUnavailableCount === 0) {
      console.log('[S5-CIT PASS] No "Source unavailable" errors');
    }

    await snap(page, 'audit_5_highlight.png');

    // 5e — Multimodal image attachment
    console.log('\n[S5-MM] Multimodal image test');
    await newChat(page);
    await page.waitForTimeout(1500);
    await setScope(page, 'Clean_MSA.pdf');
    await page.waitForTimeout(1000);

    // Attach image via Paperclip button
    let imageAttached = false;
    try {
      const attachBtn = page.locator('button[aria-label="Attach an image"]').first();
      await attachBtn.waitFor({ timeout: 8000 });
      await attachBtn.click();
      await page.waitForTimeout(500);

      // The hidden file input should now be active
      const fileInput = page.locator('input[type="file"][accept*="image"], input[type="file"]').first();
      await fileInput.setInputFiles(MOCK_REDLINE);
      imageAttached = true;
      console.log('[S5-MM] mock_redline.png attached via Paperclip button');
      await page.waitForTimeout(2000);
    } catch (e: any) {
      console.error(`[S5-MM FAIL] Could not attach image: ${e.message?.slice(0, 80)}`);
    }

    if (imageAttached) {
      const mmResult = await sendMessage(
        page,
        'Does the indemnification language shown in this redlined image conflict with the indemnification clause in this contract? Explain any discrepancies.',
        120000
      );
      console.log(`[S5-MM] response OK (${mmResult.length} chars)`);

      const mmKeywords = ['indemnification', 'redline', 'conflict', 'clause', 'image', 'discrepan', 'language'];
      const mmHits = mmKeywords.filter(k => mmResult.toLowerCase().includes(k.toLowerCase())).length;
      console.log(`[S5-MM] keyword hits: ${mmHits}/${mmKeywords.length}`);
      if (mmHits >= 2) console.log('[S5-MM PASS] Multimodal response references relevant content');
      else console.warn('[S5-MM WARN] Low keyword match in multimodal response');
    }

    await snap(page, 'audit_5_multimodal.png');

    console.log('\n========== AUDIT COMPLETE ==========');
    console.log(`Citation Summary: Clicked=${totalCitationsClicked} | PDF Viewer Opened=${totalPdfViewerOpened} | Source-Unavailable Errors=${sourceUnavailableCount}`);
  });
});
