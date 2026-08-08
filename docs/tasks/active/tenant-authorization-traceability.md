# Tenant authorization and traceability hardening

- Status: Complete
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `70459b4`
- Last known implementation commit: `72ad73f`

## Goal

Close verified tenant-authorization and traceability gaps in Celery/Supervisor task ownership, persisted chat-session contract scope, audit/error attribution, and the standalone MCP boundary. Add focused regression tests and document the resulting trust policies.

## Non-goals

- Browser or chat UI repair
- Document Analysis debugging
- RAG/search redesign or model-provider changes
- Attachments or production deployment
- Destructive graph/index cleanup
- Full external MCP authentication if no repository-supported principal mapping exists

## Acceptance criteria

- [x] Task and Supervisor status/stream access is tenant-owned, cross-process, and fail-closed.
- [x] Persisted chat-session scope is authoritative and mismatches are rejected without mutating history.
- [x] Production-reachable audit/error records in the audited paths cannot fall back to tenant-looking demo/default identities.
- [x] In-process MCP identity is server-derived and standalone production behavior fails closed unless authenticated externally.
- [x] Security behavior is covered by focused regression tests and documented in the system map/ADRs.

## Invariants affected

- Authenticated tenant identity
- Neo4j tenant predicates
- Redis namespacing and failure behavior
- Celery/Supervisor ownership across processes
- Chat session/contract scope
- MCP trust boundaries and correlation tracing
- Audit/error tenant and actor attribution
- Sensitive-data logging and secret handling
- API compatibility and production resource constraints

## Expected components/files

- `backend/api/contract_intelligence.py`
- `backend/api/supervisor_api.py`
- `backend/tasks.py`, `backend/celery_app.py`
- Redis ownership helper under existing infrastructure/shared boundaries
- `backend/main.py`, chat session/repository routes
- `backend/infrastructure/audit_logger.py`, error/audit callers
- `backend/mcp_server.py`, `backend/mcp/`, MCP tests
- Focused backend tests
- `docs/SYSTEM_MAP.md`, security/trust documentation, and ADRs where required

## Verification commands

- Focused new tenant/security tests
- Existing chat-session, MCP, Celery/task, Supervisor, and migration tests as affected
- `backend/.venv/bin/ruff check backend --select=E9,F821`
- Full backend pytest suite if practical
- Frontend build only if frontend/shared contract files change
- `git diff --check`
- Added-line secret and `.env`-overlap scan

## Decisions

- Unknown, expired, corrupt, and other-tenant task identifiers are indistinguishable (`404`); unavailable shared ownership storage is `503` before Celery result access.
- Task ownership uses real Redis, an environment/tenant/task namespace, and a 24-hour TTL aligned with Celery result expiry. In-process fallback is rejected for authorization.
- Persisted chat-session scope is server-authoritative and exact normalized mismatches return `409` before streaming or message persistence (ADR-001).
- Audit/error writes require explicit tenant or explicit system scope; reads predicate tenant, and unrestricted prompt/tool/exception payloads are not persisted.
- In-process MCP binds JWT-derived tenant outside generic tool arguments. Standalone MCP is explicit trusted-local development only and production-disabled until external principal mapping exists (ADR-002).
- Correlation IDs provide traceability, never authorization.

## Work completed

- Preserved the 20-file persistent-session slice in commit `d2b3c94`.
- Preserved the 12-file cross-tool governance layer in commit `70459b4`.
- Confirmed grouped-extraction research remains untracked and untouched.
- Added tenant-owned Celery/Supervisor status authorization and tenant-scoped progress pub/sub in `8b120a3`.
- Added server-authoritative session scope, tenant-attributed/sanitized audit and error paths, authenticated in-process MCP identity, and production-closed standalone MCP in `72ad73f`.
- Added focused regressions and ADR-001/ADR-002; refreshed the system map and CI gap analysis.

## Work remaining

- No in-scope implementation remains.
- A separate compatibility-focused task must remove or authenticate remaining default-tenant parameters in older contract/document/PDF/chunk/CUAD paths and review unscoped Section/Clause parent matches.
- Real Redis/Celery multi-process integration and external MCP authentication remain intentionally unimplemented/unverified.

## Failing checks

None in the final verification set. The full backend suite passed with 759 tests and 1 intentional skip; deprecation warnings remain in the baseline.

## Checks not run

- Browser/E2E, live providers, production, destructive migrations, real Redis/Celery worker integration, external MCP clients, and the grouped-extraction benchmark are outside this task.
- Frontend build was not rerun for Stage 2 because no frontend source/client contract changed. The Stage 1 build passed before disk pressure required removal of ignored `frontend/node_modules` and `frontend/dist`.

## Changed/uncommitted files

- No task-owned files should remain uncommitted after the handoff commit.
- Untouched unrelated research remains untracked: `research/benchmark/pilot_grouped_extraction.py` and `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- The host volume reached zero allocatable space during the final commit. Only reproducible caches, `frontend/dist`, and ignored `frontend/node_modules` were removed; approximately 252 MiB remained afterward. Reclaim host space before reinstalling dependencies or restoring the stack.
- Ownership is mock-tested; a real Redis/broker/worker journey must verify TTL, enqueue rollback, status, and worker/process behavior.
- Remaining legacy default-tenant parameters and unscoped parent-ID matches are explicitly unresolved production-reachable risks; legacy sample-data migrations must not be rerun to infer ownership.
- Standalone MCP has no external authentication. Production remains intentionally disabled rather than pretending a tenant argument is authority.
- UI behavior, Document Analysis errors, chat timeouts/cancellation, actual model attribution, and the reported backend outage were not investigated here.

## Recommended next action

Reclaim host disk space, reinstall locked frontend dependencies, then restore the complete local stack and browser-verify Claude's persistent-session implementation while reproducing the chat and Document Analysis failures. Include a real Redis/Celery ownership smoke check without modifying production.
