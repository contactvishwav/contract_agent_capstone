# MLOps evaluation harness and autonomous student/teacher routing

- Status: Phase 5 complete and verified locally; Phase 6 not started
- Active owner/tool: Claude
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `112506b`
- Last known commit: (Phase 5 commit follows this handoff update)

## Goal

Two independent, sequentially-gated upgrades requested directly by the user
(not derived from an existing roadmap doc):

1. **Phase 5 - Offline evaluation harness.** A golden-dataset-driven
   Recall@K/nDCG retrieval evaluation script, with results exposed to ADMIN
   users in the UI, closing the "zero offline RAG evaluation" gap (previously
   only Playwright UI assertions covered retrieval quality).
2. **Phase 6 - Autonomous student/teacher chat routing.** A deterministic
   semantic router that picks a low-cost "student" model for simple
   extraction prompts and a high-reasoning "teacher" model for complex
   redline/synthesis prompts, with a visible model-tier badge in the chat UI.

Each phase is implemented, browser-verified with a dedicated Playwright spec
and screenshot, and committed before the next phase starts (user's explicit
execution rule).

## Non-goals

- Production deployment/push - all verification is against the already-running
  local Compose stack (`backend` on `BACKEND_PORT=8001`, `ui` on `3000`,
  `neo4j`, `redis`, `worker`).
- Clause/section-level retrieval evaluation (would require the full CUAD
  intelligence pipeline, including the HITL human-review gate, to have run
  against every fixture contract - out of scope for this harness's first cut).
- An embedding-similarity ("true" semantic) router. Given every chat turn
  would pay an extra embedding-API call/latency for classification, and the
  router's decision must be deterministic for Playwright to assert on
  reliably, Phase 6 uses a deterministic keyword/complexity heuristic over
  the raw prompt text instead.
- Any change to the two protected grouped-extraction research files.
- Changing the default chat model for existing users/tests - "Auto" is an
  additive, explicitly-selected dropdown entry, not a new default.

## Acceptance criteria

- [x] Phase 5: `backend/tests/evals/golden_dataset.json` - 10 real queries
  against the 5 existing fixture PDFs (`data/*.pdf`), each with expected
  target filename(s).
- [x] Phase 5: `backend/scripts/evaluate_retrieval.py` computes Recall@K and
  nDCG@K against the real local backend/Neo4j, writes a results artifact.
  Run locally against `http://localhost:8001` (the already-running Compose
  stack): real Recall@3 = 90%, nDCG@3 = 90% (9/10 golden queries hit; one
  genuine miss - `msa-payment-terms` returned zero results, a real
  dynamic-relevance-floor filtering effect, not a harness bug).
- [x] Phase 5: `GET /api/admin/evaluations` (ADMIN-only) serves the latest
  results artifact.
- [x] Phase 5: an admin-only in-app page renders Recall/nDCG metrics
  (`frontend/src/pages/AdminEvaluationsPage.tsx`, reached via a new
  "Evaluations" nav entry, ADMIN-gated).
- [x] Phase 5: `frontend/e2e/mlops-eval.spec.ts` passes locally and produces
  `frontend/test-results/phase5_eval_metrics.png` (gitignored, local proof
  only - matches this repo's existing `frontend/.gitignore` convention of
  never committing generated Playwright screenshots).
- [x] Phase 5 committed before Phase 6 starts.
- [ ] Phase 6: `backend/routing_service.py` classifies a prompt into
  student/teacher and resolves a concrete registry model id for each.
- [ ] Phase 6: chat UI shows a "Student Model"/"Teacher Model" badge derived
  from the actually-executed model's registry `cost_class`.
- [ ] Phase 6: `frontend/e2e/dynamic-routing.spec.ts` passes locally and
  produces `frontend/test-results/phase6_student_routing.png` and
  `frontend/test-results/phase6_teacher_routing.png`.
- [ ] Phase 6 committed.

## Invariants affected

- Server-authoritative model registry (`backend/model_registry.py`, ADR-006) -
  the router only ever resolves to an existing, already-validated
  `ModelSpec`; it does not bypass `validate_model`/production-allowed checks.
- Tenant isolation - the eval script authenticates as a normal, freshly
  bootstrapped tenant through the real `/api/auth/register` flow, same as
  every Playwright spec; no direct DB writes, no `default-tenant`.
- Provider-neutral chat persistence/attribution - routing changes which
  model id is passed into the existing `runner()` path; it does not change
  what `requested_model`/`actual_model` mean or how they're persisted.
- RBAC - the new evaluations endpoint is gated with the existing
  `requires_role(UserRole.ADMIN)` dependency, same as Phase 4's review-queue
  routes.

## Expected components/files

- `backend/tests/evals/golden_dataset.json`, `backend/scripts/evaluate_retrieval.py`
- `backend/api/admin_evaluations_api.py` (new router), `backend/main.py` (include_router)
- `backend/routing_service.py`
- `backend/api/model_registry_api.py` (expose the "auto" pseudo-entry)
- `backend/main.py` `/api/run/` (resolve `"auto"` before `validate_model`)
- `frontend/src/pages/AdminEvaluationsPage.tsx`, `frontend/src/services/adminEvaluationsApi.ts`
- `frontend/src/lib/useRouter.ts`, `frontend/src/App.tsx`, `frontend/src/components/layout/Navigation.tsx`
- `frontend/src/components/features/contracts/message.tsx` (model-tier badge)
- `frontend/e2e/mlops-eval.spec.ts`, `frontend/e2e/dynamic-routing.spec.ts`
- `backend/tests/test_routing_service.py`, `backend/tests/test_admin_evaluations_api.py`

## Verification commands

- `backend/.venv/bin/python -m pytest backend/tests/test_routing_service.py backend/tests/test_admin_evaluations_api.py backend/tests/test_model_registry.py -q`
- `backend/.venv/bin/ruff check backend --select=E9,F821`
- `npm run build` (frontend/)
- `npm run test:e2e -- mlops-eval.spec.ts` / `dynamic-routing.spec.ts` (frontend/), against the already-running local stack
- `git diff --check` and an added-line secret scan before each phase's commit

## Decisions

- Golden dataset is document-level only (see non-goals) - clause/section
  eval is a documented future extension once a seeded clause corpus exists.
- "Auto" routing is additive to the existing manual model dropdown, not a
  replacement default, to keep blast radius local to this feature.
- Model tier badge is derived client-side from `ModelOption.cost_class`
  (`low` -> Student, `medium`/`high` -> Teacher) - reuses data already
  returned by `/api/models` and already flowing through `actual_model`
  end-to-end, instead of threading a new field through `runner()`'s SSE/
  persistence boundary.

## Work completed

Phase 5, end to end:
- Golden dataset + `evaluate_retrieval.py`, run against the real local
  backend (`BACKEND_PORT=8001`), real Neo4j-backed document search.
- `GET /api/admin/evaluations` (`requires_role(ADMIN)`), reading the
  script's JSON artifact.
- `AdminEvaluationsPage.tsx` + `adminEvaluationsApi.ts` + nav entry
  (`useRouter.ts`'s `PageType`, `App.tsx`, `Navigation.tsx`, ADMIN-gated).
- Backend unit tests (`test_admin_evaluations_api.py`: 401/403/empty/happy
  path, 4 tests) and the Playwright spec (`mlops-eval.spec.ts`), both green.
- Frontend `npm test` (45/45) and `npm run build` re-verified after the
  route/nav changes.

## Work remaining

Phase 6 (routing_service.py, "auto" model option, chat-message tier badge,
its own Playwright spec) - not started.

## Failing checks

None.

## Checks not run

- Full backend `pytest` suite (only the focused new/adjacent tests above
  were run - full-suite run deferred to avoid the runtime cost of
  unrelated live-provider-backed tests for a two-file addition).
- Full existing Playwright suite (would double as a real-money LLM-call
  regression check across every other spec; out of scope for this
  additive, non-default-changing feature).

## Changed/uncommitted files

Phase 5 files, about to be committed:
- `backend/main.py` (include the new router)
- `backend/api/admin_evaluations_api.py`, `backend/scripts/evaluate_retrieval.py`
- `backend/tests/evals/golden_dataset.json`, `backend/tests/evals/latest_results.json`
- `backend/tests/test_admin_evaluations_api.py`
- `frontend/src/App.tsx`, `frontend/src/lib/useRouter.ts`, `frontend/src/components/layout/Navigation.tsx`
- `frontend/src/pages/AdminEvaluationsPage.tsx`, `frontend/src/services/adminEvaluationsApi.ts`
- `frontend/e2e/mlops-eval.spec.ts`
- This task contract

Left untouched/unstaged (pre-existing, not this task's): `.DS_Store`,
`docker-compose.yml`, `frontend/package.json`/`package-lock.json`,
`frontend/audit_snapshots/*.png`, `chat_architecture_audit.md`,
`showcase_readiness_audit.md`, `response.txt`, `test_neo4j_direct.py`,
`verify_gemini.py`, deleted `.env.example` - all present before this task
started (see this repo's `git status` at session start).

## Risks/questions

- The local stack's `backend`/`ui`/`worker` containers have no bind mount
  for `./backend`/`./frontend` (only named volumes for PDF/attachment
  storage) - a plain `docker compose up` does not pick up source changes.
  Confirmed via the running `x-develop.watch` block requires `docker
  compose watch`, which was not active. `docker compose up -d --build
  <service>` (used here for `backend`, `worker`, `ui`) is required after
  any edit before that edit is exercised by curl/Playwright.
- `GET /api/admin/evaluations` serves a results artifact baked into the
  backend image at build time (no bind mount) - re-running
  `evaluate_retrieval.py` alone does not update what the running container
  serves; the backend must be rebuilt afterward. Documented in the
  script's own module docstring as a known local-dev limitation of the
  "batch job writes artifact, dashboard reads latest" pattern.

## Recommended next action

Start Phase 6: `routing_service.py`, wire `"auto"` into `/api/run/` and
`/api/models`, add the chat-message tier badge, write
`dynamic-routing.spec.ts`, verify, commit.
