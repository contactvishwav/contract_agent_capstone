# Product workflow corrections: analysis, lifecycle, citations, and chat UX

- Status: Complete
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `46bdf26`
- Last known commit: `abce606`

## Goal

Repair contract selection and persisted analysis restoration; add a safe,
tenant-scoped contract lifecycle; preserve and display validated chat evidence;
render untrusted Markdown safely; add persistent session rename; and clean up
the connected selector/composer interactions with real browser verification.

## Non-goals

- Output Guard graph repair, production deployment, RAG redesign, provider switching,
  attachments, destructive database cleanup, or broad visual refactoring
- Hard deletion without an accepted retention/purge design
- A full analysis-history or formal version-lineage UI unless correctness requires it

## Acceptance criteria

- [x] Recent Contract controls select the intended contract and never display stale analysis.
- [x] Latest completed analysis restores from authenticated persistence without a model call.
- [x] Unanalyzed, running, partial, failed, fallback, and planned states remain distinct.
- [x] Authorized archive/removal hides a contract from normal use and permits corrected re-upload.
- [x] Cross-tenant/unauthorized lifecycle and analysis access fail without disclosure.
- [x] Cache, active selection, search/chat options, tasks, sessions, and audit retention follow a documented lifecycle rule.
- [x] Assistant citations originate from real retrieved evidence, validate, persist, restore, and render safely.
- [x] Markdown is rendered with a maintained safe renderer and unsafe HTML/URLs cannot execute.
- [x] Session rename is validated, tenant scoped, persistent, accessible, and model-free.
- [x] Selectors are bounded/scrollable and do not obscure the composer; suggestions are consistent.
- [x] Navigation, refresh, tenant changes, and request races preserve correctness.
- [x] Focused checks, frontend build, blocking Ruff, full backend suite, and browser acceptance pass or exact failures are recorded.
- [x] Protected grouped-extraction files remain untouched; nothing is pushed or deployed.

## Invariants affected

- Authenticated tenant identity and Neo4j/Redis scoping
- Server-authoritative chat-session contract scope
- Grounding, citation validation, and provider-neutral persistence
- Analysis execution-path/model identity and partial/failure honesty
- Audit retention, archive/search behavior, cache invalidation, and task safety
- API/frontend schema synchronization, untrusted-content rendering, and secrets

## Expected components/files

- Document Analysis selection/state, contract history, analysis APIs and persistence
- Contract repository/upload duplicate logic, lifecycle API/service, caches and tests
- Chat tool evidence, SSE/message schema, session persistence/routes, rename API
- Markdown/citation/session UI plus focused frontend tests
- Lifecycle ADR or decision record, system map, and this handoff

## Verification commands

- Focused backend analysis/lifecycle/chat/citation/tenant tests
- Focused frontend component/security/accessibility tests
- `npm test`, `npm run build`, touched-file/global lint baseline
- `uv run ruff check . --select=E9,F821`, `uv run pytest -q`
- `git diff --check`, added-line secret scan, Compose health
- Real Neo4j/Redis/Celery checks and browser-controlled acceptance with minimal Gemini calls

## Decisions

- Accepted ADR-003: product removal is tenant-scoped soft archive, not physical deletion; active byte hashes define duplicates, and corrected same-name content is permitted.
- Do not let browser caches become the source of truth for analyses or citations.
- Prefer a truthful Sources section before unsupported inline claim-level citation placement.
- Citations are derived from current-turn structured tool results and revalidated against authenticated tenant plus active Contract state on creation and restoration; page is omitted when unavailable.
- Assistant output uses maintained GFM rendering with sanitization. User messages and raw tool payloads remain plain/raw text.

## Work completed

- Verified expected branch/HEAD and only the two protected research files untracked.
- Verified the canonical local Compose stack is healthy with backend port override 8001.
- Read repository governance, source-of-truth map, system map, working protocol,
  relevant accepted ADRs, and the completed local browser-verification handoff.
- Reproduced the Recent Contracts behavior: pointer selection worked but lacked button semantics; analysis state was local-only and did not restore from persistence.
- Added immutable encrypted AnalysisRun persistence, latest-analysis retrieval without a model call, explicit lifecycle/task/legacy states, stale-request suppression, one accessible selected-contract model, and tenant-keyed selection convenience state (`2eef523`).
- Added ADR-003 and the ADMIN/DELETE-only soft archive lifecycle, active SHA-256 duplicate identity, active search/chat/list filtering, running-task refusal, tenant cache invalidation, and corrected re-upload behavior (`240c015`).
- Implemented provider-neutral citations with tenant/active revalidation at creation and restore, encrypted persistence, SSE and Sources rendering, and richer retrieval provenance.
- Implemented safe GFM Markdown, persistent validated inline session rename, bounded selectors/session list, accessible selector labels, dead Gemini 1.5 option removal, and consistent suggestion copy.
- Browser acceptance archived the prior MSA/SOW records and re-uploaded the same bytes successfully as active contracts `UPLOADED_96CCBBB2_20260808` and `UPLOADED_21915AEF_20260808`. Both completed through `plan_execution_engine` with `planned_execution=true`; independent 7-clause and 5-clause results survived repeated switching and refresh.
- Browser chat acceptance produced validated MSA-only, SOW-only, and multi-contract Sources panels from real excerpts; an unsupported answer had no trusted Sources. Persisted multi-tool evidence now deduplicates to four unique chunk sources on restore.
- Browser Markdown showed real bold headings and list items. `MSA Payment Review` survived navigation, refresh, logout, and login. The session list measured 208px client height versus 252px scroll height; the contract menu was measured against the textarea and corrected from a 6px overlap to no 2D overlap. Escape returned focus and Arrow/Enter selected MSA.
- Read-only Neo4j verification found one immutable AnalysisRun for each active fixture, the two predecessor contracts archived, contract-scoped predecessor sessions archived, three encrypted citation-bearing assistant messages, and tenant-scoped ChatMessage rows.
- Final verification: full backend `786 passed, 1 skipped`; frontend `12 passed`; blocking Ruff passed; touched frontend lint had zero errors/two Fast Refresh warnings; production frontend build passed with the known CSS minifier and bundle-size warnings; `git diff --check` passed; Compose services were healthy; and the pending `analysis_run_schema` migration applied while the preceding 12 were skipped as already applied.

## Work remaining

- Separate Output Guard graph-state repair and release-blocker verification.
- Optional follow-ups: formal contract version lineage/purge policy, precise citation deep links or inline claim placement, and global frontend lint-baseline cleanup.

## Failing checks

- Global frontend lint remains a pre-existing report-only baseline: 27 errors and 8 warnings in unrelated and earlier files, as measured at this task's checkpoint. Current task-final touched Chat files have zero lint errors and two Fast Refresh warnings. (Corrected 2026-08-09: this figure had gone stale by several intervening commits; the current repo-wide baseline is 17 errors, 7 warnings - see `docs/CI_GAP_ANALYSIS.md`.)

## Checks not run

- Production build-platform/deployment/smoke/rollback checks were not run because deployment is explicitly out of scope.
- No live provider matrix, destructive purge, Output Guard repair, citation deep-link navigation, or formal version-lineage test was run because each is deferred.
- Browser automation could not drive the hidden native file input, so fresh fixture uploads used the same authenticated local upload API; archive, selection, analysis, Chat, rename, refresh, login, and selector behavior were exercised in the real browser. Cross-tenant lifecycle/retrieval was verified by focused automated tests rather than creating another live tenant.

## Changed/uncommitted files

- None. Only the two protected grouped-extraction research files remain untracked.
- Protected and untouched: `research/benchmark/pilot_grouped_extraction.py` and
  `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- Output Guard remains a separate known release blocker and must not be repaired here.
- Global frontend lint and npm audit (3 low, 1 moderate, 9 high, 1 critical) remain baselines requiring separate triage; no automatic dependency rewrite was attempted.
- Citation UI provides truthful evidence panels, not precise inline claim-to-source placement or deep-link navigation. Page metadata is absent in the current fixture graph and is not invented.

## Recommended next action

Repair and verify the Contract Chat Output Guard graph-state failure as the next release-blocking task.
