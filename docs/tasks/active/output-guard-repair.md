# Output Guard graph-contract repair

- Status: Complete
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `fac6f9466536bb36c83d92ca8df95a9607f6db01`
- Last verified implementation commit: `eb429df48f5d93719bbb309a130e7e4e7988b332`

## Goal

Repair and verify Contract Chat Output Guard execution so safety and grounding
validation uses the intended raw model boundary, infrastructure failures cannot
be interpreted as passes, and every turn reaches a truthful terminal state.

## Non-goals

- Production deployment or production access
- RAG redesign, provider switching, attachments, or broad chat refactoring
- Changes to grouped-extraction research artifacts

## Acceptance criteria

- [x] Deterministically reproduce and root-cause the pre-fix `InvalidUpdateError`.
- [x] Use the correct raw-model invocation contract for output validators.
- [x] Fail closed on validator failure and distinguish pass, rejection, timeout,
  cancellation, malformed/empty output, and persistence failure.
- [x] Preserve terminal state and ordering through SSE, persistence, and restore.
- [x] Keep audit/metrics metadata tenant-scoped and free of sensitive content.
- [x] Pass focused/full checks and local browser acceptance, or record exact gaps.
- [x] Commit only task-owned tracked files; do not push or deploy.

## Invariants affected

- Authenticated tenant identity and audit scoping
- Grounding, safety, traceability, and human-review framing
- Provider-neutral chat persistence, ordering, and model attribution
- SSE/API/frontend contract synchronization
- Sensitive-data logging and production-resource constraints

## Expected components/files

- `backend/governance/base.py`, output validators, and focused guard tests
- `backend/main.py`, chat persistence/API schema, SSE tests, and guard metrics
- Minimal frontend terminal-state restoration/rendering tests if required
- `docs/SYSTEM_MAP.md` and this handoff

## Verification commands

- Focused Output Guard, chat/session/SSE, audit, hallucination, and tenant tests
- Frontend component tests/build only if frontend files change
- `uv run ruff check . --select=E9,F821`
- `uv run pytest -q`
- `git diff --check` and added-line secret scan
- Compose health plus local browser pass/failure acceptance

## Decisions

- Fail closed. Validator infrastructure failures are not successful validation.
- The reproduced LangGraph failure is an invocation-boundary defect: Output Guard
  validators received a compiled Contract Chat `MessagesState` graph and invoked
  it with a string. The repair will use the raw provider model, not change graph
  reducers or suppress `InvalidUpdateError`.
- Stored and streamed terminal states must use safe bounded messages; raw prompts,
  source text, tool results, provider payloads, and stack traces are excluded.
- Generated assistant content is not released before all required output validators
  pass. Output validation is sequential and deterministic; an infrastructure
  failure outranks a policy rejection and no later pass can overwrite either.
- Production validation uses the validators' async provider boundary so timeout
  and cancellation cancel the awaited operation instead of leaving a background
  validation that can later record a false pass.
- The accepted outcome policy and rollback boundary are recorded in ADR-004.

## Work completed

- Verified branch/HEAD, classified the two protected untracked files, and checked
  local Compose health.
- Reproduced `InvalidUpdateError` with sanitized input before a provider call and
  inspected the compiled graph nodes/edges.
- Traced the current fail-open result through audit, SSE, citation, and persistence.
- Added an explicit raw-model lookup and converted safety/grounding validators to
  strict, async-capable, fail-closed classifiers. Empty or malformed classifier
  responses and provider exceptions are validation failures; missing evidence is
  a grounding rejection; retrieved evidence is delimited as untrusted data.
- Added explicit guard statuses, ordered per-validator results, safe audit metadata,
  and bounded Prometheus labels. Raw output, source text, provider payloads, and
  exception messages are not included in audit or operational logs.
- Buffered generated assistant text until validation and durable final-message
  persistence succeed. SSE emits bounded `error` and `end` events for terminal
  failures. Nullable `terminal_status` flows through Neo4j, API types, frontend
  restoration, and alert rendering without backfilling or breaking legacy rows.
- Added deterministic backend and frontend regressions for the original graph
  contract error, raw-model use, malformed/provider failures, aggregation,
  rejection, timeout/cancellation, persistence failure, restoration, and composer
  recovery.
- Browser-verified a grounded pass, refresh/session restoration, a safe prompt-
  guard rejection, and a real Output Guard timeout. The first timeout experiment
  exposed a background-thread continuation; production validation was changed to
  true async invocation, and the repeated timeout produced no later pass/audit.
- Restored the local guard timeout to 60 seconds and confirmed the backend, worker,
  UI, Neo4j, and Redis services healthy. No production system was accessed.
- Committed the coherent implementation/test/config checkpoint as `eb429df`
  (`fix: make contract chat output validation fail closed`).

## Work remaining

- No implementation work remains in this task. Production-readiness review must
  separately evaluate provider latency/quota, deployment resources, rollout, and
  rollback; this local task does not authorize deployment.

## Failing checks

- None in the declared final verification.
- During development, the first full backend run had three legacy expectations
  that asserted fail-open behavior; they were updated to the accepted fail-closed
  contract, after which focused and full suites passed. No check was weakened.

## Verification outcomes

- Focused backend guard/chat/audit tests: 31 passed, 134 warnings in 8.64 seconds.
- Final full backend suite: 810 passed, 1 skipped, 2272 warnings in 49.26 seconds.
- Blocking Ruff checks over the changed Python set: passed.
- Frontend focused tests: 6 passed; full frontend tests: 14 passed across 5 files.
- Frontend production build: passed (1986 modules); retained the existing CSS
  parser and large-chunk warnings.
- Lint over changed frontend files: zero errors and one existing Fast Refresh
  warning in `provider.tsx`.
- Local browser/runtime acceptance: grounded pass and restoration; safe rejection
  and restoration; forced timeout, composer recovery, durable timeout state, and
  confirmed cancellation without a late validation pass.

## Checks not run

- No production deployment, production smoke/rollback, destructive database
  operation, or broad paid/live-model evaluation matrix.
- No deliberately harmful live prompt or unsafe generated answer was sent to a
  provider. Output-Guard rejection/failure was exercised deterministically; the
  browser rejection used the safe out-of-scope Prompt Guard path.
- Repository-wide frontend lint and dependency-audit baselines were not re-triaged;
  this task ran lint on its changed frontend files plus full tests/build.

## Changed/uncommitted files

- Task-owned implementation, tests, Compose configuration, ADR, system map, and
  this handoff are the only files intended for the task commits.
- Protected and untouched/untracked: `research/benchmark/pilot_grouped_extraction.py`
  and `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- Live validation adds provider latency/cost because the existing design genuinely
  performs safety and hallucination model calls; local acceptance will remain small.
- The branch is 16 commits ahead of `origin/main`, not the stale expected count of 11.
- Provider/model attribution is selected-path attribution, not independent
  provider-response attestation.
- The 60-second local guard timeout and local service health do not prove fit for
  the resource-constrained production host.

## Recommended next action

Review ADR-004 and this implementation for production readiness, including guard
latency/quota, metrics/alerts, deployment resource fit, rollout, and rollback. Do
not deploy solely because the local checks pass.
