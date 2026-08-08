# Product workflow corrections: analysis, lifecycle, citations, and chat UX

- Status: In progress
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `46bdf26`
- Last known commit: `240c015`

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

- [ ] Recent Contract controls select the intended contract and never display stale analysis.
- [ ] Latest completed analysis restores from authenticated persistence without a model call.
- [ ] Unanalyzed, running, partial, failed, fallback, and planned states remain distinct.
- [ ] Authorized archive/removal hides a contract from normal use and permits corrected re-upload.
- [ ] Cross-tenant/unauthorized lifecycle and analysis access fail without disclosure.
- [ ] Cache, active selection, search/chat options, tasks, sessions, and audit retention follow a documented lifecycle rule.
- [ ] Assistant citations originate from real retrieved evidence, validate, persist, restore, and render safely.
- [ ] Markdown is rendered with a maintained safe renderer and unsafe HTML/URLs cannot execute.
- [ ] Session rename is validated, tenant scoped, persistent, accessible, and model-free.
- [ ] Selectors are bounded/scrollable and do not obscure the composer; suggestions are consistent.
- [ ] Navigation, refresh, tenant changes, and request races preserve correctness.
- [ ] Focused checks, frontend build, blocking Ruff, full backend suite, and browser acceptance pass or exact failures are recorded.
- [ ] Protected grouped-extraction files remain untouched; nothing is pushed or deployed.

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

## Work remaining

- Run full backend/frontend gates, rebuild and migrate the local stack, then complete browser acceptance with fresh MSA/SOW data and minimal live model calls.
- Commit the citation/chat UX checkpoint and final documentation handoff.

## Failing checks

- None in focused verification. Local OpenTelemetry attempts to export to absent Phoenix and logs after pytest closes output; test results remain passing.

## Checks not run

- Full backend suite, global frontend lint/tests, local migrations after rebuild, and final browser acceptance remain pending.

## Changed/uncommitted files

- Citation/chat UX implementation and tests listed by `git status`.
- Protected and untouched: `research/benchmark/pilot_grouped_extraction.py` and
  `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- Archive semantics must account for duplicate detection, active tasks, sessions,
  search/vector results, audit retention, and encrypted source-data retention.
- Citation metadata may be lost at multiple tool/SSE/persistence boundaries; do not
  infer provenance that the retrieval path cannot prove.
- Output Guard remains a separate known release blocker and must not be repaired here.

## Recommended next action

Run full automated gates, rebuild the local Compose stack, apply the registered migrations,
and browser-verify the full corrected workflow with fresh data.
