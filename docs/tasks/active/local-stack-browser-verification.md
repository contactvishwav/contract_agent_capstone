# Local stack and persistent-session browser verification

- Status: Complete locally; no production action authorized or performed
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `a7ad6f3`
- Last verified implementation commit: `cd56411`
- Handoff/source-map commit: `720fffb`

## Goal

Restore the supported local Compose stack, browser-verify persistent Contract
Chat with fresh tenant-owned contracts, diagnose and repair the reported chat
and Document Analysis failures, and exercise Redis/Celery ownership boundaries
with real local services.

## Non-goals

- Production access, deployment, or production configuration changes
- RAG/search redesign, provider switching, attachments, quote replies, or broad refactoring
- Destructive Neo4j/index cleanup, Docker pruning, volume deletion, or legacy/sample migration execution
- Weakening tenant checks, authentication, guards, tests, or explicit planning/fallback identity

## Acceptance checklist

- [x] The complete development stack runs through Compose. Host port 8000 was
  already owned by an unrelated restart-always project, so the supported
  repeatable command is `BACKEND_PORT=8001 docker compose up -d --build`.
- [x] Browser-to-backend proxy and direct backend health checks succeed.
- [x] Fresh tenants were created through the supported registration flow.
- [x] `Clean_MSA.pdf` and `Clean_SOW.pdf` were uploaded through authenticated
  supported flows; the final tenant IDs were
  `UPLOADED_319D38A3_20260808` and `UPLOADED_C974351E_20260808`.
- [x] Chat scope options show All Contracts, `Clean_MSA.pdf`, and `Clean_SOW.pdf`.
- [x] Multiple sessions for one contract and an All-Contracts session coexist
  without eager/duplicate empty sessions.
- [x] Active and different session rows load/retry the correct persisted history.
- [x] History survives app navigation, refresh, and logout/login.
- [x] A tenant change clears client-owned contract/session state.
- [x] Fresh foreign tenants receive empty document lists and 404 for foreign
  contract, chat-session, task, Supervisor status, and Supervisor stream IDs.
- [x] Suggested questions are accessible buttons and use the normal SSE submit path.
- [x] Live Gemini answers render on the first turn and persist in canonical order.
- [x] Requested/actual model attribution is represented as `gemini-2.5-flash`.
- [x] Fresh `Clean_MSA.pdf` Document Analysis completed through the intended
  `PlanExecutionEngine` path with explicit identity.
- [x] Planned/fallback/explicit-traditional identity survives domain, task,
  persistence, API, and frontend conversion.
- [x] Real Redis ownership marker, TTL, corrupt/missing/unavailable behavior,
  worker receipt, owner/foreign status, and redacted progress channels were exercised.
- [x] Frontend component tests and production build pass.
- [x] Blocking Ruff and the full backend suite pass.
- [x] The two grouped-extraction research files remain untouched and untracked.
- [x] Production remains unchanged.

## Invariants affected

- Authenticated tenant identity and tenant predicates
- Server-authoritative chat-session contract scope
- Provider-neutral ordered message persistence and replay
- Requested versus actual model attribution
- Default `PlanExecutionEngine` versus explicit fallback identity
- Redis ownership/progress namespacing and Celery process boundaries
- Neo4j schema/migration compatibility and populated-data safety
- API/frontend contract synchronization, sensitive logging, and secrets

## Decisions

- Preserve contract scope, chat-session identity, conversation title, and model
  as separate concepts.
- Bootstrap recent contract metadata from authenticated `GET /api/documents`;
  tenant-scoped browser storage is only a cache.
- Mount tenant-owned frontend providers inside the authentication boundary and
  keep `ChatProvider` alive across in-app navigation.
- Create a session lazily on first send. A newly created zero-message session
  must not be detail-fetched while its first turn is streaming; restored
  sessions with persisted messages are bootstrapped automatically, and every
  explicit row click retries detail loading.
- Use a deterministic first-prompt title (first 72 characters) rather than an
  additional paid model call.
- Persist a safe terminal AI state for stream exceptions/cancellation so a
  user turn is not left as a misleading unmatched success.
- Keep analysis fallback behavior, but expose the actual path explicitly.
- Require authenticated tenant scope for optimized/enhanced CUAD database and
  cache calls. Internal optimized-to-enhanced degradation remains explicit.
- Keep port 8000 as the Compose default while allowing `BACKEND_PORT` override.

## Initial state and reproduced defects

- Branch/HEAD matched the expected `feat/persistent-chat-sessions` at `a7ad6f3`,
  five commits ahead of `origin/main`; only the two protected research files
  were initially untracked.
- Docker Desktop was wedged, an old ad-hoc Uvicorn process accepted but did not
  answer, Vite was unavailable, and UI/backend probes failed. Two exact runaway
  pytest PIDs from the previous task were terminated; no data, images, or
  volumes were removed.
- Canonical backend publication then failed because unrelated container
  `monitoring-fastapi-1` already owned host port 8000. It was not stopped.
- The browser reproduced: active session metadata without messages; active-row
  no-op; navigation/refresh loss; page-local Chat provider remount; raw contract
  IDs as labels; missing server-backed contract history; noninteractive sample
  text; and cross-tenant stale session metadata after logout/login.
- A first-turn race was found after the main repair: automatic detail bootstrap
  fetched a just-created empty session and cleared optimistic/streamed messages.
- The reported Document Analysis “Server error” did not reproduce after backend
  reachability was restored. The failing layer was the unavailable backend, not
  the analysis endpoint or worker. Subsequent real SOW and MSA analyses completed.
- Live output-guard validator failures logged provider exception text containing
  prompt/source context. Logging was made content-safe. The validators still
  raise `InvalidUpdateError` and fail open; that structural repair is deferred.
- Live CUAD runs exposed missing tenant propagation first to precedent matching,
  then to optimized deviation-cache calls. Both call paths are now scoped.

## Work completed

### Runtime/backend

- Added authenticated, tenant-scoped `GET /api/documents` for frontend bootstrap.
- Added tenant predicate to chunk-parent matching.
- Threaded tenant through optimized/enhanced CUAD precedent and deviation paths.
- Added explicit `execution_path` and `planned_execution` through the analysis
  domain result, orchestrator fallback/explicit paths, task result, Neo4j
  persistence, status API, and UI.
- Added resilient chat stream terminal-state persistence for exceptions and
  cancellation, without logging prompt/contract/tool content.
- Made guard exception logging content-safe.
- Replaced the worker’s inherited HTTP health check with broker-mediated Celery ping.
- Made the local backend host port configurable without changing its default.

### Frontend

- Moved tenant-owned providers inside the authenticated tenant key and kept chat
  message state across navigation.
- Replaced tenant-unscoped contract history with authenticated server bootstrap
  plus tenant-scoped cache.
- Made session list/active storage tenant-scoped and auth-reactive.
- Added automatic persisted-detail restore, active-row retry, explicit
  loading/error states, distinct title/scope labels, and New Chat behavior.
- Added controlled prompt queue so suggestion buttons use the normal submit path.
- Locked scope to the server-authoritative active-session scope.
- Prevented empty new-session detail bootstrap from clearing the first streamed turn.
- Displayed analysis execution path and actual model.
- Added the smallest component-test harness (Vitest 3, jsdom 26, Testing Library).

## Runtime and data evidence

- Final Compose state: backend, UI, Neo4j, Redis, and worker healthy; Phoenix up.
  Backend is published at 8001 locally due the unrelated 8000 owner, while the
  UI continues to reach service `backend:8000` on the Compose network.
- Migration ledger: all 12 registered migrations applied. Chat session/message
  constraints and tenant indexes exist. All indexes are ONLINE.
- Seven native vector indexes match the repository configuration: `Chunk`,
  `Clause`, `Section`, three `Contract` embedding properties, and
  `PolicyDocument`; all use 1536 dimensions and COSINE on vector provider 2.0.
- Existing graph inspection found older `Chunk` nodes without their own tenant
  property; they remain scoped through the owning tenant-predicated Document.
  New chunk parent matching now includes tenant. No migration/data cleanup ran.
- Final test tenant has four browser-created sessions. The inspected session
  persisted sequence 1 user, 2 tool call, 3 matching tool result, 4 AI; the AI
  row records `gemini-2.5-flash` and the two tool rows share a call ID.
- Clean MSA task `bc80d25f0fc745af9ff14a81916faed7` completed in 35.26s:
  7 clauses, 4 violations, risk 100/CRITICAL, `execution_path=plan_execution_engine`,
  `planned_execution=true`, `analysis_method=optimized_phase3`, model
  `gemini-2.5-flash`.
- Real ownership marker value was `v1`, TTL was 86,076 seconds at inspection,
  and the key contained neither raw tenant nor task ID. Missing and isolated
  corrupt markers failed closed; an unavailable Redis connection raised
  `TaskOwnershipUnavailable`; tenant-specific progress channels differed and
  contained no raw tenant/contract IDs. The isolated corrupt marker was deleted.

## Commits

- `4c94549` — `fix: harden chat and analysis runtime boundaries`
- `50d9500` — `fix: restore tenant-scoped chat journeys`
- `60b545e` — `fix: preserve first chat turn while streaming`
- `cd56411` — `chore: allow local backend port override`
- `720fffb` — `docs: record local browser verification handoff`

## Verification outcomes

- `uv run pytest -q`: **766 passed, 1 skipped** in 49.48s; warnings only.
- Focused backend regression command: **30 passed** in 6.46s.
- `uv run ruff check . --select=E9,F821`: **passed**.
- `npm test`: **6 passed** across 3 files after the stream-race regression.
- `npm run build`: **passed**, 1,729 modules. Existing Tailwind/CSS minifier
  warning: `Unexpected ")"` in a generated `:has(:is())` selector.
- `npm run lint`: **failed existing baseline**, 26 errors and 7 warnings as
  measured at this task's checkpoint (corrected 2026-08-09: stale by several
  intervening commits; current repo-wide baseline is 17 errors, 7 warnings -
  see `docs/CI_GAP_ANALYSIS.md`). The
  failures are pre-existing broad `no-explicit-any`, unused variable, hook, and
  fast-refresh findings; no new test file is listed. This task did not weaken or
  broadly rewrite the lint baseline.
- `git diff --check`: **passed**.
- Added-line secret-pattern scan: **no matches**.
- Compose config with `BACKEND_PORT=8001`: **valid**.
- Direct backend 8001 and UI-proxied 3000 health: **healthy** with Redis, Neo4j,
  and performance components healthy.
- Broker-mediated Celery inspect: **1 node online, pong**.
- Browser-controlled manual acceptance: **passed** for upload, contract options,
  suggestions, first-turn stream rendering, multiple/same-contract/all-contract
  sessions, switching/retry, navigation, refresh, logout/login, tenant clearing,
  real Gemini chat, and real MSA analysis.

## Checks not run

- No production build/deploy/smoke, GCP/e2-micro resource validation, rollback,
  or production endpoint access; explicitly out of scope.
- No broad live-model evaluation; only focused local Gemini calls needed for chat
  and analysis acceptance.
- No destructive migration, legacy sample migration, vector-index recreation,
  volume deletion, Docker prune, or database cleanup.
- No automated browser E2E suite was added; manual browser evidence plus focused
  component tests were used because the repository has no existing E2E harness.
- Chat timeout/cancel terminal UI was not forced against a live paid request;
  backend terminal persistence has focused regression coverage.

## Uncommitted/protected files

- Protected and untouched; these are the only files expected to remain untracked:
  `research/benchmark/pilot_grouped_extraction.py` and
  `research/benchmark/pilot_grouped_extraction_sample.json`.

## Remaining risks/questions

- Output-guard Safety/Hallucination LangGraph validators raise
  `InvalidUpdateError` and currently fail open. Sensitive exception content is
  no longer logged, but validator correctness requires a separate focused task.
- Frontend ESLint has an established red baseline (26 errors/7 warnings as of
  this task; corrected 2026-08-09 - current repo-wide baseline is 17 errors,
  7 warnings, see `docs/CI_GAP_ANALYSIS.md`).
- `npm install` reports 14 dependency advisories (3 low, 1 moderate, 9 high,
  1 critical); no unreviewed `npm audit fix` was run.
- Local development uses intentionally insecure fallback JWT/encryption keys when
  those variables are absent; production startup validation remains the control.
- Celery dev worker runs as root and with concurrency 8; production uses the
  separately constrained configuration. Local success does not prove e2-micro fit.
- Older `Chunk` nodes lack a direct tenant property, and Section/Clause parent
  matching deserves a separate tenant-boundary audit before any schema cleanup.
- Navigation remains in-memory, so refresh returns to Document Analysis before
  the user reopens Chat; persisted session/message restoration itself is verified.
- All-Contracts query “active” semantics returned zero for the fresh records;
  this is a data/status semantic question, not a session-persistence failure.

## Recommended next action

Repair and verify the Output Guard validator graph contract so safety and
hallucination checks do not fail open, with focused tests proving safe failure
classification and no sensitive logging. Keep RAG redesign, provider switching,
attachments, production deployment, and broad refactoring out of that task.
