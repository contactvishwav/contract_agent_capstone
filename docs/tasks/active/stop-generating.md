# Stop Generating for Contract Chat

- Status: Complete
- Active owner/tool: Codex
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `d1a45060d53cf819200b864dd2744aaf4037af27`
- Last verified implementation commit: `42d3fdc0d75c5f1ffcc9fa9fa2d7f4d51555512b`

## Goal

Add a visible, accessible Stop generating control that aborts the active
authenticated Contract Chat SSE request, preserves one truthful `cancelled`
terminal state when cancellation wins, prevents late output, and restores the
composer for a fresh request.

## Non-goals

- PDF citation/viewer or model-registry work; those begin only after this task is
  separately committed and verified.
- Production deployment, public cancellation endpoints, or provider-delay hooks.
- Changes to grouped-extraction research artifacts.

## Acceptance criteria

- [x] Stop generating replaces Send throughout the active browser request.
- [x] Stop aborts the active stream, suppresses late chunks, and cannot be invoked twice.
- [x] Cancellation wins only before the validated answer is durably persisted.
- [x] Exactly one terminal state persists and restores as a restrained cancellation.
- [x] Session switch, new chat, navigation/unmount, and logout cancel safely.
- [x] A new request uses a fresh controller and can complete normally.
- [x] Contract/session/tenant boundaries remain server-authoritative.
- [x] Focused backend/frontend tests and real browser acceptance pass.
- [x] Task-owned changes are committed separately; nothing is pushed or deployed.

## Invariants affected

- Authenticated tenant/session scope and server-authoritative contract scope
- Provider-neutral chat ordering and terminal-state persistence
- Output Guard fail-closed buffering and SSE/API/client synchronization
- Audit/metrics accuracy, sensitive logging, and secrets

## Expected components/files

- `frontend/src/components/features/contracts/provider.tsx`
- `frontend/src/components/features/contracts/input.tsx`
- `frontend/src/components/features/contracts/SessionSwitcher.tsx`
- Focused frontend cancellation tests
- `backend/main.py` and focused terminal-state tests
- `docs/SYSTEM_MAP.md` and this handoff

## Verification commands

- Focused frontend Stop/session-switch tests
- Focused backend cancellation/Output Guard terminal-state tests
- Touched-file frontend lint and blocking Ruff
- Frontend build and proportionate backend/frontend suites
- `git diff --check`, added-line secret scan, Compose health
- Real browser cancellation, 60-second late-answer observation, restoration, retry,
  and model-switch cancellation

## Decisions

- Navigation, session switching, new chat, logout, and Chat UI unmount cancel the
  current browser request. Session switching waits for browser-stream cleanup
  before loading another session; navigation/logout cannot delay teardown, so the
  backend's durable terminal state is authoritative on return.
- Cancellation uses an authenticated POST bound to an opaque active-run UUID plus
  the server-validated tenant/session; the identifier is never authorization. A
  matching server acknowledgement is required before the UI aborts its SSE stream.
- Durable final-answer persistence is the cancellation boundary. If it already
  happened, the backend must not replace the answer or emit cancelled metrics.
- Client disconnect alone is insufficient evidence of server cancellation. The UI
  displays `Generation stopped` only after the authenticated cancellation route has
  interrupted the executing task and confirmed durable terminal persistence.
- Active asyncio tasks are process-local. This is compatible with the documented
  single-process e2-micro API deployment; multi-process API scaling requires a new
  distributed cancellation design rather than pretending a Redis flag can cancel a
  task executing in another process.

## Work completed

- Verified a clean tracked tree at `d1a4506`; only the two protected research
  artifacts are untracked.
- Confirmed the client creates an `AbortController` but exposes no Stop control,
  intentional abort currently falls into generic failure handling, and session
  switching does not coordinate with the active stream.
- Confirmed `resilient_runner` catches `CancelledError` and attempts tenant/session-
  scoped terminal persistence, but currently records cancelled observability even
  if a completed assistant message already won the race.
- Added a shared frontend request lifecycle with fresh per-run controllers, stable
  Stop/Stopping/Finishing states, duplicate-submit prevention, late-event rejection,
  server acknowledgement, and cleanup across stop, session switch, new chat,
  navigation/unmount, and logout.
- Added an opaque UUID run protocol and authenticated tenant/session-bound active-run
  registry. The server races generation/tool/guard awaits against cancellation,
  injects `CancelledError`, waits for one durable terminal outcome, and returns 202
  only when cancellation wins. Missing/cross-tenant/cross-session IDs return the same
  non-disclosing 404; completion returns 409 and remains authoritative.
- Changed terminal persistence to report created/already-completed/unconfirmed states
  so cancellation metrics/audit are emitted only for a genuinely persisted cancel.
- Fixed an explicit All-Contracts selection race that previously restored the first
  contract fallback before submission; deterministic and browser evidence now show
  an actual All-Contracts session.
- The first browser experiment intentionally disproved client-only abort: the UI hid
  the stream, but refresh restored a late successful answer. After the handshake
  repair, a new MSA run returned 202 only after `cancelled` audit/persistence, showed
  no late answer for over 60 seconds, and restored `Generation stopped` after refresh.
- A fresh question in the cancelled MSA session completed normally with grounded
  citations. A genuine All-Contracts run was cancelled by switching sessions and
  restored as cancelled when reopened.
- Committed the implementation/test checkpoint as `42d3fdc`
  (`feat: add server-acknowledged chat cancellation`).

## Work remaining

- No prerequisite implementation remains. The PDF-citation/model-selection task may
  begin after this work is committed separately.
- A second live configured-model cancellation was not possible: the local backend
  reports Gemini configured and OpenAI unavailable. Model switching/fresh-controller
  behavior is covered deterministically; provider availability will be corrected by
  the next task's server-authoritative registry.

## Failing checks

- None in final declared verification.
- The first browser acceptance failed by restoring a late success after client-only
  abort. That evidence drove the server handshake; the repaired journey passed.

## Verification outcomes

- Focused backend cancellation/Output Guard suite: 33 passed, 129 warnings.
- Full backend suite: 814 passed, 1 skipped, 2271 warnings in 52.92 seconds.
- Final full frontend suite: 22 passed across 6 files. Production build passed
  (1986 modules) with the existing CSS minifier and large-chunk warnings. Touched-
  file lint had zero errors and one existing Fast Refresh warning in `provider.tsx`.
- Blocking Ruff over changed backend files passed.
- Real browser: visible Stop; acknowledged MSA cancellation; 60-second no-late-answer
  observation; navigation/refresh restoration; successful follow-up; real All-
  Contracts scope; session-switch cancellation and restoration.

## Checks not run

- No second live provider/model cancellation because no second compatible provider
  credential is configured locally. No fake success or paid slow-provider request.
- No production, destructive data operation, deployment, push, or multi-process API
  cancellation test.

## Changed/uncommitted files

- Backend/frontend implementation and tests are committed at `42d3fdc`. This task
  record and the system map are pending the handoff documentation commit.
- Protected and untouched: `research/benchmark/pilot_grouped_extraction.py` and
  `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- Browser disconnect propagation must be demonstrated against the real local ASGI
  stack; this is now demonstrated through the authenticated 202 handshake and durable
  restored state rather than inferred from connection closure.
- Cancellation during synchronous durable persistence is completion-wins by design.
- The active-run registry is deliberately process-local; multi-process API scaling is
  a release architecture change, not silently supported.

## Recommended next action

Commit this prerequisite separately, then begin the PDF-citation/model-selection task
with a new active task record and read-only audit.
