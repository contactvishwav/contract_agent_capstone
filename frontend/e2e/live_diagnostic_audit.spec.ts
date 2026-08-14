/**
 * LIVE PRODUCTION DIAGNOSTIC AUDIT
 * Target: https://contract-intel.duckdns.org/
 * Credentials: demo / password123
 *
 * DIAGNOSTIC ONLY — no assertions will throw hard failures.
 * Every step documents its outcome. Screenshots captured at every stage.
 * Run: npx playwright test e2e/live_diagnostic_audit.spec.ts --headed
 */

import { test, Page } from '@playwright/test';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BASE_URL = 'https://contract-intel.duckdns.org';
const USERNAME = 'demo';
const PASSWORD = 'password123';
const SNAPSHOT_DIR = path.resolve(__dirname, '../audit_snapshots');

const PDFS = {
  playbook:  path.resolve(__dirname, '../../../data/Contract_Policy_Playbook.pdf'),
  salesforce: path.resolve(__dirname, '../../../data/Salesforce_MSA.pdf'),
  cleanMsa:  path.resolve(__dirname, '../../../data/Clean_MSA.pdf'),
  cleanSow:  path.resolve(__dirname, '../../../data/Clean_SOW.pdf'),
};
const MOCK_REDLINE = path.resolve(__dirname, './fixtures/mock_redline.png');

async function snap(page: Page, filename: string) {
  const filePath = path.join(SNAPSHOT_DIR, filename);
  await page.screenshot({ path: filePath, fullPage: true });
  console.log(`[SNAPSHOT] ${filePath}`);
}

async function clickAndWait(page: Page, selector: string, ms = 3000) {
  try {
    await page.click(selector, { timeout: 10000 });
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(ms);
  } catch (e: any) {
    console.warn(`[WARN] clickAndWait(${selector}): ${e.message}`);
  }
}

async function safeType(page: Page, selector: string, text: string) {
  try {
    await page.fill(selector, text, { timeout: 8000 });
  } catch (e: any) {
    console.warn(`[WARN] safeType(${selector}): ${e.message}`);
  }
}

async function findFirst(page: Page, selectors: string[]): Promise<string | null> {
  for (const sel of selectors) {
    try {
      await page.locator(sel).first().waitFor({ timeout: 3000 });
      return sel;
    } catch { continue; }
  }
  return null;
}

const SEND_BTN = [
  'button:has-text("Send")',
  'button[type="submit"]',
  '[data-testid="send-button"]',
  'button[aria-label*="send" i]',
];

const CHAT_INPUT = [
  'textarea[placeholder*="message" i]',
  'textarea[placeholder*="Ask" i]',
  'textarea',
  '[contenteditable="true"]',
  '[data-testid="chat-input"]',
];

// ─────────────────────────────────────────────────────────────────────────────

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

  // ─── STAGE 1 ────────────────────────────────────────────────────────────────
  test('STAGE 1 — Authentication', async () => {
    const page = sharedPage;
    console.log('\n========== STAGE 1: AUTH ==========');

    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);
    console.log(`[S1] title="${await page.title()}" url=${page.url()}`);

    const body0 = await page.textContent('body').catch(() => '');
    if (body0?.includes('502') || body0?.includes('Bad Gateway'))
      console.error('[S1 FAIL] 502 on initial load');

    const userSel = await findFirst(page, [
      'input[name="username"]', 'input[type="text"]',
      'input[placeholder*="username" i]', '#username',
    ]);
    if (userSel) { await safeType(page, userSel, USERNAME); console.log(`[S1] user field: ${userSel}`); }
    else console.error('[S1 FAIL] No username field');

    const passSel = await findFirst(page, ['input[name="password"]', 'input[type="password"]', '#password']);
    if (passSel) { await safeType(page, passSel, PASSWORD); console.log(`[S1] pass field: ${passSel}`); }
    else console.error('[S1 FAIL] No password field');

    const signInSel = await findFirst(page, [
      'button[type="submit"]', 'button:has-text("Sign In")',
      'button:has-text("Login")', 'button:has-text("Log In")',
    ]);
    if (signInSel) { await page.click(signInSel); console.log(`[S1] clicked sign-in: ${signInSel}`); }
    else console.error('[S1 FAIL] No Sign In button');

    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(4000);

    const postUrl = page.url();
    const postBody = await page.textContent('body').catch(() => '');
    console.log(`[S1] post-login url=${postUrl}`);

    if (postBody?.includes('502') || postBody?.includes('Bad Gateway'))
      console.error('[S1 FAIL] 502 after login');
    else if (postUrl.includes('/login') || postUrl === BASE_URL + '/')
      console.warn('[S1 WARN] Still on login page — auth may have failed');
    else
      console.log('[S1 PASS] Redirected — auth succeeded');

    if (postBody?.includes('Signing in'))
      console.error('[S1 FAIL] Stuck "Signing in..." state');

    await snap(page, 'audit_1_login_attempt.png');
  });

  // ─── STAGE 2 ────────────────────────────────────────────────────────────────
  test('STAGE 2 — Document Upload', async () => {
    const page = sharedPage;
    console.log('\n========== STAGE 2: UPLOAD ==========');

    const navSel = await findFirst(page, [
      'a:has-text("Document Analysis")', 'a:has-text("Document Upload")',
      'a:has-text("Upload")', 'nav a[href*="document"]',
      '[data-testid="nav-document"]', 'button:has-text("Document")',
    ]);
    if (navSel) { await clickAndWait(page, navSel, 2000); console.log(`[S2] nav: ${navSel}`); }
    else {
      await page.goto(`${BASE_URL}/document-analysis`, { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(2000);
      console.warn('[S2 WARN] Tried /document-analysis directly');
    }

    const toggleSel = await findFirst(page, [
      'label:has-text("Multi-Level")', 'label:has-text("Embeddings")',
      'input[type="checkbox"][name*="embedding" i]',
      'button[role="switch"]',
    ]);
    if (toggleSel) {
      try { await page.click(toggleSel); console.log(`[S2] Toggled: ${toggleSel}`); }
      catch (e: any) { console.warn(`[S2 WARN] toggle click: ${e.message}`); }
    } else {
      console.warn('[S2 WARN] Multi-Level Embeddings toggle not found');
    }

    const uploadList = [
      { label: 'Contract_Policy_Playbook.pdf', file: PDFS.playbook },
      { label: 'Salesforce_MSA.pdf',           file: PDFS.salesforce },
      { label: 'Clean_MSA.pdf',                file: PDFS.cleanMsa },
      { label: 'Clean_SOW.pdf',                file: PDFS.cleanSow },
    ];

    for (const pdf of uploadList) {
      console.log(`[S2] Uploading ${pdf.label}...`);
      const fileInputSel = await findFirst(page, ['input[type="file"]', 'input[accept*="pdf"]']);
      if (!fileInputSel) { console.error(`[S2 FAIL] No file input for ${pdf.label}`); continue; }

      try {
        await page.setInputFiles(fileInputSel, pdf.file);
        const uploadBtn = await findFirst(page, [
          'button:has-text("Upload")', 'button:has-text("Process")',
          'button:has-text("Analyze")', 'button[type="submit"]',
        ]);
        if (uploadBtn) { await page.click(uploadBtn); console.log(`[S2] upload btn clicked for ${pdf.label}`); }

        await page.waitForLoadState('networkidle', { timeout: 90000 }).catch(() => {});
        await page.waitForTimeout(5000);

        const bodyNow = await page.textContent('body').catch(() => '');
        const ok = bodyNow?.includes('processed successfully') || bodyNow?.includes('successfully');
        const err = bodyNow?.includes('error') || bodyNow?.includes('failed') || bodyNow?.includes('Error');
        console.log(`[S2] ${pdf.label}: success=${ok} error=${err}`);
      } catch (e: any) {
        console.error(`[S2 FAIL] Exception for ${pdf.label}: ${e.message}`);
      }
      await page.waitForTimeout(2000);
    }

    await snap(page, 'audit_2_all_uploads_analyzed.png');
  });

  // ─── STAGE 3 ────────────────────────────────────────────────────────────────
  test('STAGE 3 — Enhanced Search', async () => {
    const page = sharedPage;
    console.log('\n========== STAGE 3: SEARCH ==========');

    const searchNav = await findFirst(page, [
      'a:has-text("Enhanced Search")', 'a:has-text("Search")',
      'nav a[href*="search"]', '[data-testid="nav-search"]',
    ]);
    if (searchNav) { await clickAndWait(page, searchNav, 2000); console.log(`[S3] nav: ${searchNav}`); }
    else {
      await page.goto(`${BASE_URL}/enhanced-search`, { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(2000);
    }

    const SEARCH_BTN = ['button:has-text("Search")', 'button[type="submit"]', '[data-testid="search-button"]'];
    const SEARCH_INPUT = ['input[placeholder*="search" i]', 'input[placeholder*="Search" i]', 'input[type="search"]', 'input[type="text"]'];

    // 3a – Document tab
    console.log('[S3a] Document tab');
    const docTab = await findFirst(page, ['button:has-text("Document")', '[role="tab"]:has-text("Document")']);
    if (docTab) { await clickAndWait(page, docTab, 1500); }

    const searchInput = await findFirst(page, SEARCH_INPUT);
    if (searchInput) await safeType(page, searchInput, 'termination');

    const ctSel = await findFirst(page, ['select[name*="contract" i]', 'select[id*="contract" i]', 'select']);
    if (ctSel) {
      await page.selectOption(ctSel, { label: 'MSA' }).catch(async () => {
        await page.selectOption(ctSel, { value: 'MSA' }).catch(() => console.warn('[S3a] Cannot set MSA'));
      });
    }

    const partiesSel = await findFirst(page, ['input[placeholder*="parties" i]', 'input[name*="parties" i]']);
    if (partiesSel) await safeType(page, partiesSel, 'ConsultCorp');

    const sb3a = await findFirst(page, SEARCH_BTN);
    if (sb3a) { await page.click(sb3a); await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {}); await page.waitForTimeout(4000); }

    const body3a = await page.textContent('body').catch(() => '');
    console.log(`[S3a] pdf-filenames=${body3a?.includes('.pdf')} raw-uuids=${/[0-9a-f]{8}-[0-9a-f]{4}/.test(body3a ?? '')}`);
    await snap(page, 'audit_3_search_document.png');

    // 3b – Section tab
    console.log('[S3b] Section tab');
    const sectTab = await findFirst(page, ['button:has-text("Section")', '[role="tab"]:has-text("Section")']);
    if (sectTab) { await clickAndWait(page, sectTab, 1500); } else console.error('[S3b FAIL] no Section tab');

    const paymentSel = await findFirst(page, ['label:has-text("Payment Terms")', 'input[type="checkbox"]:near(:text("Payment Terms"))']);
    if (paymentSel) await page.click(paymentSel).catch(() => {});
    const liabSel = await findFirst(page, ['label:has-text("Liability")', 'input[type="checkbox"]:near(:text("Liability"))']);
    if (liabSel) await page.click(liabSel).catch(() => {});

    const sb3b = await findFirst(page, SEARCH_BTN);
    if (sb3b) { await page.click(sb3b); await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {}); await page.waitForTimeout(4000); }

    const body3b = await page.textContent('body').catch(() => '');
    console.log(`[S3b] section-results=${body3b?.toLowerCase().includes('section')}`);
    await snap(page, 'audit_3_search_section.png');

    // 3c – Clause tab
    console.log('[S3c] Clause tab');
    const clauseTab = await findFirst(page, ['button:has-text("Clause")', '[role="tab"]:has-text("Clause")']);
    if (clauseTab) { await clickAndWait(page, clauseTab, 1500); } else console.error('[S3c FAIL] no Clause tab');

    const cuadSel = await findFirst(page, ['button:has-text("CUAD")', ':text("CUAD Clause Types")', 'details summary:has-text("CUAD")']);
    if (cuadSel) { await page.click(cuadSel); await page.waitForTimeout(1000); }

    const termConv = await findFirst(page, ['label:has-text("Termination For Convenience")', ':text("Termination For Convenience")']);
    if (termConv) await page.click(termConv).catch(() => {});
    const postTerm = await findFirst(page, ['label:has-text("Post-Termination")', ':text("Post-Termination Obligations")']);
    if (postTerm) await page.click(postTerm).catch(() => {});

    const sb3c = await findFirst(page, SEARCH_BTN);
    if (sb3c) { await page.click(sb3c); await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {}); await page.waitForTimeout(4000); }

    const body3c = await page.textContent('body').catch(() => '');
    const purpleCount = await page.locator('[class*="purple"],[class*="violet"]').count().catch(() => 0);
    console.log(`[S3c] confidence=${body3c?.includes('confidence')||body3c?.includes('%')} purple-badges=${purpleCount}`);
    await snap(page, 'audit_3_search_clause.png');

    // 3d – Relationship tab
    console.log('[S3d] Relationship tab');
    const relTab = await findFirst(page, ['button:has-text("Relationship")', '[role="tab"]:has-text("Relationship")']);
    if (relTab) { await clickAndWait(page, relTab, 1500); } else console.error('[S3d FAIL] no Relationship tab');

    const relParties = await findFirst(page, ['input[placeholder*="parties" i]', 'input[name*="parties" i]', 'input[type="text"]']);
    if (relParties) await safeType(page, relParties, 'ConsultCorp, ClientCo');

    const sb3d = await findFirst(page, SEARCH_BTN);
    if (sb3d) { await page.click(sb3d); await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {}); await page.waitForTimeout(4000); }

    const body3d = await page.textContent('body').catch(() => '');
    console.log(`[S3d] rel-data=${body3d?.includes('ConsultCorp')||body3d?.includes('ClientCo')||body3d?.includes('relationship')}`);
    await snap(page, 'audit_3_search_relationship.png');
  });

  // ─── STAGE 4 ────────────────────────────────────────────────────────────────
  test('STAGE 4 — Multi-Doc Chat', async () => {
    const page = sharedPage;
    console.log('\n========== STAGE 4: MULTI-DOC CHAT ==========');

    const chatNav = await findFirst(page, ['a:has-text("Contract Chat")', 'a:has-text("Chat")', 'nav a[href*="chat"]']);
    if (chatNav) { await clickAndWait(page, chatNav, 2000); }
    else { await page.goto(`${BASE_URL}/contract-chat`, { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {}); await page.waitForTimeout(2000); }

    // Set scope
    const scopeSelect = page.locator('select').first();
    try {
      await scopeSelect.waitFor({ timeout: 5000 });
      await scopeSelect.selectOption({ label: 'All contracts' });
      console.log('[S4] scope=All contracts');
    } catch {
      const allSel = await findFirst(page, ['button:has-text("All")', ':text("All contracts")']);
      if (allSel) await page.click(allSel);
      else console.warn('[S4 WARN] Cannot set scope to All');
    }

    await page.waitForTimeout(1000);

    const inputSel = await findFirst(page, CHAT_INPUT);
    if (inputSel) {
      await safeType(page, inputSel, 'Across the Policy Playbook, the Salesforce MSA, the Clean MSA, and the Clean SOW, summarize the differing notice periods required for termination.');
      console.log('[S4] query typed');
    } else console.error('[S4 FAIL] No chat input');

    const sendSel = await findFirst(page, SEND_BTN);
    if (sendSel) { await page.click(sendSel); console.log('[S4] sent'); }
    else { await page.keyboard.press('Enter'); console.log('[S4] Enter sent'); }

    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(8000);

    let found = false;
    for (let i = 0; i < 12 && !found; i++) {
      const body = await page.textContent('body').catch(() => '');
      if (body?.includes('Response withheld')) { console.error('[S4 FAIL] Response withheld'); found = true; }
      else if (body?.includes('notice') || body?.includes('termination') || body?.includes('days')) { console.log('[S4 PASS] Response received'); found = true; }
      else { console.log(`[S4] waiting... ${(i+1)*10}s`); await page.waitForTimeout(10000); }
    }
    if (!found) console.warn('[S4 WARN] No response after 120s');

    await snap(page, 'audit_4_all_contracts_chat.png');
  });

  // ─── STAGE 5 ────────────────────────────────────────────────────────────────
  test('STAGE 5 — Deep Dives + Citation + Multimodal', async () => {
    const page = sharedPage;
    console.log('\n========== STAGE 5: DEEP DIVES ==========');

    async function setScopeToDocument(label: string) {
      const sel = page.locator('select').first();
      try {
        await sel.waitFor({ timeout: 3000 });
        await sel.selectOption({ label }).catch(async () => {
          const opts = await sel.locator('option').allTextContents();
          console.log(`[S5] scope options: ${JSON.stringify(opts)}`);
          const m = opts.find(o => o.includes(label.replace('.pdf', '')));
          if (m) await sel.selectOption({ label: m });
          else console.warn(`[S5 WARN] No scope option for ${label}`);
        });
      } catch (e: any) { console.warn(`[S5 WARN] scope(${label}): ${e.message}`); }
      await page.waitForTimeout(500);
    }

    async function newChat() {
      const btnSel = await findFirst(page, [
        'button:has-text("New Chat")', 'button:has-text("New chat")',
        'button:has-text("Clear")', '[data-testid="new-chat"]',
      ]);
      if (btnSel) { await clickAndWait(page, btnSel, 1500); }
      else { await page.reload({ waitUntil: 'domcontentloaded' }); await page.waitForTimeout(2000); }
    }

    async function sendMsg(query: string): Promise<string> {
      const inputSel = await findFirst(page, CHAT_INPUT);
      if (!inputSel) { console.error(`[S5 FAIL] no input for: ${query.slice(0, 50)}`); return 'NO_INPUT'; }
      await page.fill(inputSel, query);
      const sendSel = await findFirst(page, SEND_BTN);
      if (sendSel) await page.click(sendSel);
      else await page.keyboard.press('Enter');
      console.log(`[S5] sent: "${query.slice(0, 80)}"`);
      await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(5000);
      const body = await page.textContent('body').catch(() => '');
      return body?.includes('Response withheld') ? 'WITHHELD' : 'OK';
    }

    // Ensure on chat page
    const chatNav = await findFirst(page, ['a:has-text("Contract Chat")', 'a:has-text("Chat")', 'nav a[href*="chat"]']);
    if (chatNav) await clickAndWait(page, chatNav, 2000);

    // 5a SOW
    console.log('[S5a] Clean_SOW.pdf');
    await newChat(); await setScopeToDocument('Clean_SOW.pdf');
    console.log(`[S5a] Q1=${await sendMsg('What are the specific payment milestones?')}`);
    await page.waitForTimeout(3000);
    console.log(`[S5a] Q2=${await sendMsg('What is the cure period?')}`);
    await snap(page, 'audit_5_chat_sow.png');

    // 5b Salesforce
    console.log('[S5b] Salesforce_MSA.pdf');
    await newChat(); await setScopeToDocument('Salesforce_MSA.pdf');
    console.log(`[S5b] Q1=${await sendMsg('What is the liability cap in Germany?')}`);
    await page.waitForTimeout(3000);
    console.log(`[S5b] Q2=${await sendMsg('What are the intellectual property terms?')}`);
    await snap(page, 'audit_5_chat_salesforce.png');

    // 5c CleanMSA
    console.log('[S5c] Clean_MSA.pdf');
    await newChat(); await setScopeToDocument('Clean_MSA.pdf');
    console.log(`[S5c] Q1=${await sendMsg('What is the governing law?')}`);
    await page.waitForTimeout(3000);
    console.log(`[S5c] Q2=${await sendMsg('Detail the indemnification clause.')}`);
    await snap(page, 'audit_5_chat_cleanmsa.png');

    // 5d Playbook
    console.log('[S5d] Contract_Policy_Playbook.pdf');
    await newChat(); await setScopeToDocument('Contract_Policy_Playbook.pdf');
    console.log(`[S5d] Q1=${await sendMsg('What is the maximum liability cap?')}`);
    await page.waitForTimeout(3000);
    console.log(`[S5d] Q2=${await sendMsg('What are the rules on governing law?')}`);
    await snap(page, 'audit_5_chat_playbook.png');

    // 5e Citation
    console.log('[S5e] Citation highlighting');
    let citClicked = false;
    for (const sel of ['[data-testid*="citation"]', '.citation-pill', 'button.citation', 'sup a', 'span[class*="citation"]', 'button:has-text("[1]")']) {
      const n = await page.locator(sel).count().catch(() => 0);
      if (n > 0) {
        try {
          await page.locator(sel).first().click();
          await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
          await page.waitForTimeout(3000);
          citClicked = true;
          console.log(`[S5e] Clicked citation: ${sel}`);
          break;
        } catch (e: any) { console.warn(`[S5e] citation click failed: ${e.message}`); }
      }
    }
    if (!citClicked) console.warn('[S5e WARN] No citation found');

    let pdfFound = false;
    for (const sel of ['[role="dialog"]', '[data-testid*="pdf"]', '.pdf-viewer', 'canvas', 'iframe[src*="pdf"]']) {
      if (await page.locator(sel).count().catch(() => 0) > 0) { console.log(`[S5e] PDF modal: ${sel}`); pdfFound = true; break; }
    }
    if (!pdfFound) {
      const b = await page.textContent('body').catch(() => '');
      if (b?.toLowerCase().includes('source unavailable')) console.error('[S5e FAIL] Source unavailable');
      else console.warn('[S5e WARN] No PDF modal visible');
    }
    await snap(page, 'audit_5_highlight.png');

    // 5f Multimodal
    console.log('[S5f] Multimodal');
    await newChat(); await setScopeToDocument('Clean_MSA.pdf');

    try {
      const fi = page.locator('input[type="file"]').first();
      await fi.waitFor({ timeout: 5000 });
      await fi.setInputFiles(MOCK_REDLINE);
      console.log('[S5f] image attached');
      await page.waitForTimeout(2000);
    } catch (e: any) {
      const attachBtn = await findFirst(page, [
        'button[aria-label*="attach" i]', 'button[aria-label*="image" i]',
        'button:has-text("Attach")', '[data-testid*="attach"]', 'label[for*="file"]',
      ]);
      if (attachBtn) {
        await page.click(attachBtn);
        await page.waitForTimeout(500);
        await page.setInputFiles('input[type="file"]', MOCK_REDLINE).catch(e2 => console.error(`[S5f FAIL] attach: ${e2.message}`));
      } else console.error(`[S5f FAIL] no file input: ${e.message}`);
    }

    console.log(`[S5f] mm=${await sendMsg('Does the indemnification language in this image conflict with this contract?')}`);
    await snap(page, 'audit_5_multimodal.png');

    console.log('\n========== AUDIT COMPLETE ==========');
  });
});
