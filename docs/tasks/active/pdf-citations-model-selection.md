# Authenticated PDF citations and real model selection

- Status: Implementation complete; release blocked on provider/runtime findings
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `923aedd386e0e51e61064d95e516946a3944b7cf`
- Last known commit: `22dd2d8` (architecture docs; task handoff follows)

## Goal

Replace expanded chat Sources cards with compact authenticated PDF citations at
truthful provenance, and make model selection control and report the real
provider/model boundary without silent legal-workflow fallback.

## Non-goals

- Production deployment/push, destructive backfill, public PDF URLs, invented
  pages/highlights, selectable embeddings, or cross-provider fallback
- Changes to the protected grouped-extraction research artifacts

## Acceptance status

- [x] New uploads retain encrypted exact PDF bytes and page-aware provenance.
- [x] Compact citations, authenticated PDF.js modal, exact/page/excerpt fallback,
  refresh/restoration, multiple documents, and All Contracts were verified.
- [x] JWT tenant plus active lifecycle guards source delivery; cross-tenant and
  manipulated contract IDs returned the same 404.
- [x] One server registry drives chat/analysis choices; incompatible and
  unconfigured providers are absent.
- [x] Requested/actual model/provider, terminal status, prompt version, fallback,
  and execution identity persist independently.
- [x] Flash reached Google, passed Output Guard, persisted citations, and restored.
- [x] A same-session switch reached the selected Pro boundary and failed explicitly;
  no prior attribution/history was relabelled and no fallback occurred.
- [x] Saved Document Analysis restores and retains historical actual attribution
  when the future-run selector changes.
- [x] Embeddings remain fixed at `gemini-embedding-001`, 1536 dimensions.
- [ ] Gemini 2.5 Pro returned provider HTTP 404 despite the shared Google key;
  credential presence alone is therefore insufficient model availability proof.
- [ ] Production secrets, PDF-volume backup/restore, e2-micro resource validation,
  dependency findings, and browser E2E automation remain release blockers.

## Invariants affected

- Authenticated tenant identity, active lifecycle, nondisclosing authorization
- Encrypted legal-document storage, grounding, citations, and honest provenance
- Provider-neutral history, actual attribution, Output Guard, and explicit failure
- Default/fallback identity, Celery propagation, migrations, fixed embeddings

## Decisions

- ADR-005 retains exact PDFs in AES-GCM opaque storage and encrypted `SourcePage`
  nodes. Legacy rows are never assigned invented pages; exact-hash tenant duplicate
  upload is the only automatic provenance backfill.
- The viewer is an in-memory authenticated modal. PDF bytes go directly to PDF.js;
  no public/object URL or bearer query parameter exists. Local standard-font/CMap
  assets are packaged so the text layer works without third-party requests.
- Exact highlight requires one deterministic NFKC/whitespace-normalized match;
  ambiguous, short, image-only, legacy, and missing evidence degrade truthfully.
- ADR-006 makes stable IDs and capabilities server-authoritative. Legal chat and
  analysis fail explicitly; cross-provider fallback is disabled.
- Output Guard reuses the request's selected provider/model client. Deterministic
  steps and fixed embeddings do not acquire misleading user selectors.

## Work completed

- Added page-aware extraction, encrypted source storage, additive migration,
  tenant-scoped duplicate backfill, source authorization route, and named volumes.
- Added citation creation/restoration revalidation, compact chips/previews, PDF.js
  navigation/zoom/text layer/highlight/fallback/accessibility, and local assets.
- Added server model registry/API, exact `LLMManager` routing, validation at chat,
  upload, analysis, and Supervisor entry points, plus requested/actual attribution.
- Removed silent legal provider substitution and hardcoded validator Gemini clients.
- Fixed the analysis saved-result render loop by stabilizing parent callbacks.
- Added focused backend/frontend regression coverage and updated source-of-truth docs.

## Runtime/browser evidence

- Local Neo4j/Redis/backend/worker/UI were healthy; the additive migration applied
  `pdf_source_schema` once and skipped the 13 already-applied migrations.
- The browser file chooser could not attach fixtures. The exact same idempotent
  application provenance service was run directly for the two exact-hash,
  tenant-scoped local fixture contracts; no contract content was reprocessed.
- Restored MSA citation: compact `Clean_MSA.pdf · p. 1`, authenticated viewer,
  10 highlighted PDF.js text items; close and refresh/reopen preserved the session.
- Live SOW Flash turn: compact `Clean_SOW.pdf · p. 1`, 11 highlighted items,
  citations and actual `google · gemini-2.5-flash` persisted.
- Live All Contracts turn produced clickable MSA and SOW page-1 citations; both
  opened with exact highlights.
- Authenticated source response: 200, `application/pdf`, encoded disposition,
  `private, no-store, max-age=0`, valid PDF signature. Foreign-tenant and random
  contract requests both returned 404.
- Flash provider calls returned 200 and logs show Output Guard executed and passed.
  Pro selection reached `gemini-2.5-pro`, returned 404, displayed/persisted an
  explicit generation failure, and did not silently fallback.
- Provider configuration (names only): Google configured; OpenAI, Anthropic, and
  Mistral unconfigured. Only Google choices were shown.
- Saved SOW analysis displayed `PlanExecutionEngine` and actual Flash; changing the
  selector to Pro did not relabel the historical result.

## Verification outcomes

- Full backend: `832 passed, 1 skipped, 2275 warnings in 50.44s`.
- Focused backend groups during development: PDF/citations `16 passed`; model,
  enhanced-upload, and tenant wiring `33 passed`; Output Guard/chat persistence
  `32 passed`.
- Frontend: `7` files, `29 passed`; production build passed, 1991 modules.
- Touched frontend lint: 0 errors, 1 existing Fast Refresh warning in `provider.tsx`.
- Blocking Ruff and Python compile: passed.
- Dev and production Compose config validation: passed (production used dummy
  placeholders only; no deployment or connection).
- `git diff --check`: passed. New Markdown structural/local-link check: 3 files,
  0 errors. Added-line secret-value scan: 0 findings.
- Repository-wide Markdown scan separately found five pre-existing/placeholder
  targets in historical documents; none was introduced by this task.

## Failing checks

- Live Gemini 2.5 Pro: provider HTTP 404 before completion. This was surfaced as an
  explicit terminal failure and preserved on restoration.
- Full frontend lint baseline remains 27 errors and 8 warnings outside this task's
  touched-file gate.
- `npm install` reports 14 dependency findings: 3 low, 1 moderate, 9 high,
  1 critical. No automatic audit fix was applied.

## Checks not run

- Live OpenAI, Anthropic, or Mistral calls: credentials absent.
- New paid Document Analysis run: existing planned-path result restored; Pro had
  already failed at the real provider boundary and further cost was not justified.
- Archived-source browser mutation: deterministic endpoint tests cover it; no local
  contract was archived/destructively changed for acceptance.
- OCR/image-only live fixture, production platform build/smoke/rollback, volume
  backup/restore, quota/load/cost tests, and automated browser E2E.

## Changed and uncommitted files

- Task code/docs are committed in `614c18e`, `7c7f0cc`, and `22dd2d8`; this handoff
  is the final task-owned documentation checkpoint.
- Only protected, untouched, untracked research files should remain after commit:
  `research/benchmark/pilot_grouped_extraction.py` and
  `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- Local logs warn that `JWT_SECRET_KEY` and `ENCRYPTION_KEY` are using insecure
  development defaults. Production must fail closed with managed real secrets.
- The registry currently derives configured status from credential presence; the
  Pro 404 proves a release smoke/entitlement signal is additionally required.
- PDF.js adds roughly 1.09 MB main JS, 1.04 MB worker JS, and 2.4 MB uncompressed
  font/CMap assets; code splitting and e2-micro/client validation remain.
- Citation highlights identify the exact retrieved excerpt, which can be a broad
  chunk rather than a sentence-level claim. Claim-to-span ranking is a future RAG
  quality task, not grounds for fuzzy or invented highlighting.
- The source volume can contain an opaque orphan if storage succeeds immediately
  before a graph write fails; retention/repair tooling is not yet designed.

## Recommended next action

Resolve provider availability semantics (especially the configured-but-404 Pro
model), configure real local secrets, then run a production-readiness review that
includes PDF-volume backup/restore, dependency triage, bundle/resource checks, and
automated authenticated browser E2E. Do not deploy from this task.
