# Capstone Remediation — Consolidated Summary

*A retrospective of the full arc: inherited state → correctness bugs → real LLM extraction → enterprise-readiness remediation → live end-to-end verification. Every claim below is grounded in a commit hash, a file:line, or a benchmark results file — this document does not restate the aspirational claims in `README.md`/`docs/RESUME_WRITEUP.md` without checking them against the code.*

---

## 1. What was inherited

The repository's own marketing copy (`README.md`, `docs/RESUME_WRITEUP.md`) claimed an "enterprise-grade" multi-agent system: 11+ specialized agents, a Supervisor pattern with "quality gates, error recovery, and A-F grading," a standardized MCP interface "with full data isolation and tracing," "explainable AI reasoning," "95%+ accuracy," "100% alignment with company standards," and "reducing legal review time by 70%." None of these claims had been checked against running code before this engagement began.

What was actually true, verified by reading the code:

- **Stubbed extraction.** `LLMClauseExtractor`, `LLMCUADClassifier`, and `ClauseDetectorTool` all had stubbed `_parse_llm_response()` implementations that returned `[]` regardless of input — `ClauseDetectorTool` didn't call the LLM at all. Locked in by a regression test (`test_stubbed_llm_parsers.py`, commit `66fb146`) before it was fixed, specifically so the eventual fix would be provably a fix rather than a silent behavior change.
- **Three parallel orchestration systems, not one.** The traditional LangGraph workflow (`IntelligenceOrchestrator`), the planning agent (`PlanExecutionEngine`, the actual default via `use_planning=True`), and `SupervisorAgent` (reachable only via the separate `POST /api/supervisor/workflow/execute` route) all exist independently. `RetryManager`/`CircuitBreakerManager` are only ever invoked inside `SupervisorAgent._execute_step_with_protection` — the actual default analysis path has zero reference to either class. The "quality gates" and "error recovery" advertised in the README exist, but not on the path that runs by default.
- **Broken MCP tool.** `fetch_contract_metadata` called an async repository method without `await`-ing it, so it always returned an unawaited coroutine that failed `json.dumps()` on every call. Existing tests used `MagicMock` instead of `AsyncMock`, which meant the bug was invisible to the test suite (fixed `e6c0eb1`).
- **Broken Supervisor wiring, two layers deep.** `SupervisorAgentFactory`, `PolicyWorkflowOrchestrator`, and `PolicyWorkflowSupervisor` all constructed `SupervisorAgent` with the wrong argument count — it was never successfully instantiated, likely crashing `POST /api/policies/upload` on every call (fixed `96f7250`). Fixing construction surfaced two more never-exercised bugs in the same file (a nonexistent `AuditEvent` class, a `ContentValidator` signature mismatch), fixed in the same commit. Even after construction was fixed, neither orchestrator class ever calls `self.supervisor.coordinate_workflow(...)` — the quality-gate/retry/circuit-breaker machinery is no longer *broken*, it's simply **never called**, on the one route that was fixed to construct it correctly.

This picture — stubbed extraction, disconnected orchestration systems, a crashing MCP tool, and dead-on-arrival Supervisor wiring — is the actual starting point this whole engagement worked from, not the README's description of it.

---

## 2. Phase 0 — correctness bugs

Four bugs, each following the same discipline: identify → write/confirm a regression test that fails on the current code → fix → verify the test now passes and nothing else regressed.

| # | Bug | Regression test | Commit |
|---|---|---|---|
| 1 | `fetch_contract_metadata` (`backend/mcp_server.py`) called `get_contract_by_id` (an async repo method) without `await` — always returned an unawaited coroutine, failing `json.dumps()`. Existing tests masked it with `MagicMock` instead of `AsyncMock`. | Converted `test_mcp_capabilities.py`'s mocks to `AsyncMock`; added a regression test; verified failure on pre-fix code via `git stash`. | `e6c0eb1` |
| 2 | `SupervisorAgent` constructed with the wrong argument count at all 3 call sites (`factory.py`, `policy_workflow_orchestrator.py`, `policy_workflow_supervisor.py`) — never successfully instantiated, likely crashing `POST /api/policies/upload` on every call. Fixing it surfaced 2 more never-exercised bugs (`AuditEvent` didn't exist; a `ContentValidator` signature mismatch). | `test_supervisor_construction.py` (4 tests), covering all 3 sites plus a `PolicyService()` smoke test. | `96f7250` |
| 3 | `search_prior_approved_clauses` queried a `[:CONTAINS]` relationship, but ingestion actually creates `[:CONTAINS_CLAUSE]` — precedent search always returned zero real results and silently fell through to a mock-data fallback. | `test_precedent_matcher_relationship.py` (2 tests), using a fake graph mirroring real ingestion's relationship type; confirmed 0 results pre-fix. | `d0d2860` |
| 4 | `LLMClauseExtractor`/`LLMCUADClassifier`/`ClauseDetectorTool` all had stubbed `_parse_llm_response()` returning `[]` unconditionally — `ClauseDetectorTool` never called the LLM at all. | `test_stubbed_llm_parsers.py` (3 tests, `TODO(Phase 1)` markers) — a deliberate test-first commit with no source fix, so Phase 1's fix would be provably a fix. | `66fb146` (test) → `e6553cb` (fix, Phase 1) |

Full suite at the end of Phase 0/1: 8 failed / 42 passed / 1 skipped — unchanged from the pre-Phase-0 baseline, confirming zero regressions from any of these four fixes (the 8 pre-existing failures are addressed later; see §4/§6).

---

## 3. Phase 1 — real LLM extraction

`e6553cb` replaced all three stubs with a single `LLMExtractionService` using Gemini structured output against a verified 41-type CUAD taxonomy (cross-checked against the `theatticusproject/cuad-qa` dataset, which also fixed a duplicate and a wrong category name in the old `CUAD_CLAUSE_TYPES` constant). Offsets are computed deterministically by searching the source text for the LLM's claimed `extracted_text`, not trusted from the model — `start_offset == -1` signals text that couldn't be located verbatim (a hallucination-risk flag, acted on later in §4). The 6,000-character truncation that had been silently dropping late-appearing clauses was removed entirely.

**The 497-contract Flash-Lite benchmark** (`research/benchmark/extraction_eval_results_flash-lite-497.json`), scored against real CUAD ground truth on the 5 metadata columns then available:

| Type | Precision | Recall | F1 |
|---|---|---|---|
| Document Name | 1.00 | 0.99 | 0.99 |
| Parties | 1.00 | 0.95 | 0.98 |
| Agreement Date | 0.98 | 0.63 | 0.77 |
| Effective Date | 0.90 | 0.65 | 0.76 |
| Expiration Date | 0.91 | 0.15 | 0.26 |

Root-cause breakdown on the weakest column (Expiration Date): of 274 misses, 96% (263) were the model finding nothing at all for that contract; only 4% (11) were found-but-unmatched — confirming the date-matching logic itself works, and the gap is model recall, not the matcher.

**The quota-forced model substitution**: this full-scale run used `gemini-flash-lite-latest`, not the production model `gemini-2.5-flash`, because the Gemini free tier caps `gemini-2.5-flash` at 20 requests/day/project — nowhere near enough for a 497-contract run. This substitution is explicit in every benchmark artifact's `model` field, not silently absorbed into "the benchmark."

**The gemini-2.5-flash confirmation sample** (metadata-only, stratified toward the weak date types — `research/benchmark/extraction_eval_results_gemini-2.5-flash-stratified.json`): as of this report, **44 of 90 contracts complete**, `stopped_early: true` (the same real 20/day free-tier quota wall). Numbers so far, on the production model:

| Type | Precision | Recall | F1 |
|---|---|---|---|
| Document Name | 1.00 | 1.00 | 1.00 |
| Parties | 1.00 | 1.00 | 1.00 |
| Agreement Date | 0.91 | 0.79 | 0.84 |
| Effective Date | 0.77 | 0.87 | 0.82 |
| Expiration Date | 0.76 | 0.48 | 0.59 |

Directionally consistent with the Flash-Lite root-cause analysis: the production model is meaningfully better on the weak date fields (Expiration Date F1 0.26 → 0.59) without changing the matching logic — confirming the earlier diagnosis that the gap was model capability, not the matcher. A separate, later confirmation sample scoring the *risk-relevant* categories on gemini-2.5-flash (as opposed to just these 5 metadata columns) is covered in §4, since risk-category benchmarking was itself a later punch-list item (P2 item 12) — it is currently at 5/90, blocked on the same quota mechanism (checked directly against the recurring benchmark job's checkpoint file as of this report).

---

## 4. P0–P3 — enterprise readiness remediation

This phase followed a from-scratch, file:line-grounded audit (`docs/ENTERPRISE_READINESS.md`), rating 9 domain-specific areas — explainability, accuracy, tenant isolation, reliability, scalability, observability, data integrity, testing/deployment, and cost — against what a legal/compliance team actually needs (defensibility, traceability, tenant-safe confidentiality), not a generic SaaS checklist. All 9 areas started 🔴 or 🟡; the punch list below is ordered by that audit's own priority.

### P0 — severe risk, low-to-medium effort

1. **RBAC default-to-ADMIN bug** (`backend/governance/rbac.py:56-59`). The code's own comment claimed "default to VIEWER for safety" — it actually returned `UserRole.ADMIN` on a missing `X-User-Role` header, a live privilege-escalation path granting full permissions including delete rights to any unauthenticated/misconfigured request. Fixed `2cc284f`.
2. **Cross-tenant read/delete path in `PolicyRepository`.** `get_policy_by_id`, `_get_policy_rules`, `update_policy_version`, `delete_policy` had zero `tenant_id` filter in their Cypher. Combined with bug #1, this was a *confirmed-reachable* exploit chain, not theoretical: `policy_id` is generated as `f"policy_{tenant_id}_{timestamp}"` (target tenant derivable from the ID itself), and two of the four methods were live behind real routes — `GET /{policy_id}` and `DELETE /{policy_id}` (`backend/api/policy_api.py`), gated only by a permission check that the RBAC bug let anyone bypass by simply omitting a header. All four methods tenant-scoped in `9771ee2`; delete/update also fixed to correctly report failure instead of always returning `True` when a tenant-scoped match finds nothing.
3. **`get_intelligence_status`/dashboard hardcoded `"default-tenant"`** regardless of caller — fixed to use the real caller's `tenant_id`, `b58931d`.
4. **`ChainOfThoughtAgent._risk_assessment_chain`'s `overall_risk` `NameError`** — referenced a variable never assigned, guaranteeing failure whenever CoT risk assessment ran. Fixed by computing it as the max severity-derived score across violations; a second bug (the success path never included a `pattern` key) surfaced and was fixed in the same pass. `39bf98c`.
5. **`AuditLogger.get_audit_trail`/`ErrorTracker.get_error_statistics` retrieval**, flagged by the original audit as confirmed-broken via failing tests. Investigation found no bug in the actual retrieval logic — both failures traced to a bare `MagicMock` from an unrelated test file's guard leaking into the shared Neo4j singleton (its default `__iter__` silently yields nothing), a collection-order artifact, not a real defect. Fixed by giving both tests a proper fake graph simulating real `MERGE`/`MATCH` semantics. `b4606d8`. Suite: 5 failed / 61 passed / 1 skipped / 1 error, down from 8/42/1/2.

### P1 — severe risk, medium effort

6. **`tenant_id` required, not defaulted**, on upload/analysis routes (`contracts.py`, `document_upload.py`, `enhanced_document_upload.py`) — previously silently defaulted to `"default-tenant"` on omission, letting a request read/write another tenant's bucket unnoticed. No real authenticated-identity layer exists to verify a *claimed* `tenant_id` belongs to the caller (see §6), so this scoped down to the buildable half: reject the request outright (422) rather than default. `ae7d400`, `test_tenant_id_required.py` (8 affected routes, git-stash-verified).
7. **Audit trail wired into the real analysis path**, threading `contract_id`/`tenant_id` end-to-end through *both* orchestration paths (previously dropped one layer below `analyze_contract_by_id`, never reaching either), instrumented once at the shared tool layer (`ClauseDetectorTool`, `PolicyCheckerTool`, `RiskCalculatorTool`, `RedlineGeneratorTool`) rather than duplicated per orchestration path.
8. **Clause-linked violations/risk**: a stable, deterministic `clause_id` (contract+type+offset derived, not random) threaded through `PolicyCheckerTool`'s violations and a new `critical_issue_details` field on `RiskCalculatorTool` (additive — the existing `critical_issues: List[str]` shape was left untouched since two frontend components render it directly).
9. **Honest partial-failure state**: `RiskCalculatorTool`'s except path no longer fabricates `50.0`/`"MEDIUM"`; `_generate_redlines`'s except path no longer marks `is_complete: True` on failure; `PlanExecutionEngine` tracks per-step `node_status` instead of always reporting `processing_complete: True`; the API surfaces both honestly instead of a hardcoded `analysis_complete: True`. Items 7-9 landed together as `2dd078e` (shared architectural prerequisite). Suite: 84 passed / 5 pre-existing-unrelated failed / 1 skipped, up from 69/5/1.

### P2 — medium-term

10. **Ungrounded-clause flagging**: `start_offset == -1` (Phase 1's hallucination-risk signal) was computed but never acted on. `ClauseDetectorTool` now sets a `grounded` flag per clause, threaded through violations/risk as `clause_grounded` and surfaced in the API response — flagged, not excluded, so a human reviewer can still see it but knows not to trust it at face value.
11. **Hybrid policy/risk engine**, replacing keyword-matching against a single hardcoded 6-category dict (2 of which were dead code for a CUAD-only pipeline): a small deterministic table for the 5 categories with an objective numeric threshold (Cap On Liability, Minimum Commitment, Notice Period To Terminate Renewal, Warranty Duration, Renewal Term — no LLM call, never guesses), plus `PolicyEvaluationService` for everything else (one LLM call per clause against a tenant's own uploaded `PolicyRule` set, or a labeled default set if none exists; every violation cites a real `rule_id`, independently verified against the exact set offered to the model — a hallucinated citation is discarded). `PolicyComplianceAgent` (the standalone compliance-check route) now delegates to this same engine instead of its own separate, more primitive matcher. Items 10-11: `7c6ecc6`, 10 new tests, suite 95/5/1 (up from 88).
12. **Risk-category extraction benchmark** — the headline accuracy finding of this whole engagement. The original benchmark validated only 5 metadata columns; the platform's actual value proposition (flagging risky clauses) had zero accuracy data. `prepare_risk_category_ground_truth.py` sourced real ground truth for the other 36 CUAD categories from the same dataset (497/510 contracts matched — same corpus gap as before). Full-scale result, Flash-Lite, 497 contracts (`research/benchmark/extraction_eval_results_risk-categories-flash-lite-497.json`):

    | | Avg Precision | Avg Recall | Avg F1 |
    |---|---|---|---|
    | 5 metadata columns | 0.92 | 0.68 | 0.75 |
    | 36 risk-relevant columns | 0.63 | 0.27 | 0.32 |

    5 of 36 risk categories scored a flat 0.00 F1 (Competitive Restriction Exception, Price Restrictions, Volume Restriction, Affiliate License-Licensor, Unlimited/All-You-Can-Eat-License); 20 of 36 scored below 0.30. Only 3 (Governing Law 0.98, No-Solicit Of Employees 0.82, Anti-Assignment 0.81) matched metadata-tier quality. Recall is the dominant gap — clauses missed, not fabricated. **Confirms the audit's own concern directly: accurate metadata extraction does not imply accurate risk extraction**, and until this pass, that gap had never been measured. A gemini-2.5-flash confirmation sample of this same 36-category scoring is in progress (`risk-categories-2.5-flash` run) — currently 5/90, `stopped_early: true`, blocked on the per-minute free-tier quota (confirmed via the actual `429`/`GenerateRequestsPerMinutePerProjectPerModel-FreeTier` response in the run log, not assumed). `2636751`.
13. **Rate limiting / rate-limit-aware retry.** A shared `threading.Semaphore` (`backend/shared/utils/llm_concurrency.py`, env-configurable, default 5) wired into both LLM services' outbound calls; `StepExecutor._execute_step_with_retry` now catches `ResourceExhausted` specifically and fails fast instead of retrying into it (ordinary transient failures still retry with backoff). `7d025e0`.
14. **`Contract.file_id` uniqueness constraint + migration consolidation.** Exactly 4 constraints existed repo-wide, none on `Contract`, despite `store_contract` using `CREATE` (not `MERGE`). New `run_all_migrations.py`: a single, versioned, idempotent runner tracking applied migrations via a `:SchemaMigration` node — a migration that raises is never marked applied, so a retry doesn't silently skip a broken state. `3604eaa`.
15. **`correlation_id`/`trace_id` bridging** across the HTTP/MCP process boundary — all 4 MCP tool functions now accept an optional `correlation_id`, preferred over generating a fresh, disconnected `trace_id`. Note in §6: this is a capability, not an automatic end-to-end wire-up (nothing in this codebase currently calls from FastAPI into the MCP server in-process). `68f829a`.

### P3 — infrastructure

16. **Native Neo4j vector index**, replacing brute-force `gds.similarity.cosine()`/`vector.similarity.cosine()` scans with `CREATE VECTOR INDEX` + `db.index.vector.queryNodes` across Contract/Section/Clause/Chunk/PolicyDocument embeddings. Benchmarked on 5,000 synthetic contract nodes: **~4.7x mean / ~7x median latency improvement** (`research/benchmark/vector_index_benchmark.py`).
17. **Local Neo4j + Redis in docker-compose** (replacing the default dependency on a shared public demo Neo4j instance), plus the actual root cause of `test_mcp_capabilities.py`'s long-standing collection failures: `test_ai_patterns.py`/`test_policy_system.py` each did `sys.path.insert(0, <backend/>)`, letting the local `backend/mcp/` package shadow the real third-party `mcp` SDK for the rest of the pytest process — a collection-order accident, not a missing-mock problem. Both dead `sys.path` hacks removed.
18. **CI/CD** (`.github/workflows/ci.yml`): a `test` job against a real Neo4j service container (removing the collection-order dependency on mocking luck) plus a `lint` job (ruff, blocking on E9/F821, advisory on the rest — surfaced 9 real F821 undefined-name bugs, several in security/audit-critical paths: the prompt-injection validator threw on every call, all four `PolicyAuditService` methods threw on every call). Two follow-up fixes: `[tool.pytest.ini_options] pythonpath = [".."]` in `backend/pyproject.toml` (CI's `working-directory: backend` broke absolute `backend.xxx` imports that only worked locally by accident — `b5bf892`), and `--ignore=tests/test_ai_patterns.py --ignore=tests/test_policy_system.py` for the 2 pre-existing async smoke scripts that have never had a working async-test setup (`9d9fe90`, tracked as punch-list item 22 rather than a silent exclusion, `f860dbc`). `9107fb5`.
19. **Production-grade Dockerfiles**, multi-stage (dev/production), non-root user, `fastapi run` not `fastapi dev`; frontend production serves the compiled static build via nginx with an `/api/` reverse-proxy config (without which every API call from the built frontend would 404).
20. **LLM extraction caching + cost/usage monitoring.** `LLMExtractionService.extract_clauses`/`PolicyEvaluationService.evaluate_clause` (the only two real LLM call sites) now cache on a hash of prompt version + model + inputs, finally making `CACHE_ENABLED` gate something real. New `LLMUsageTracker`, surfaced at `GET /api/monitoring/llm-usage`.
21. **Encryption at rest + PII extension to ingestion, across all 3 content locations.** `SecurityValidator`'s real bug fixed first: it set `is_valid=True` even when PII was found, making every hit invisible. New `backend/infrastructure/encryption.py` — AES-256-GCM field-level encryption (via `IKeyProvider`/`EnvKeyProvider`, swappable for a real secrets manager later) — applied in sequence to all three places contract text is actually stored:
    - `Contract.full_text` / `Clause.content` — redact-then-encrypt on write, decrypt on read (`1395b75`).
    - `Chunk.content` / `DocumentChunk.content` — the third copy, confirmed (not assumed) to have 6 live Cypher call sites doing in-Cypher `CONTAINS`/`substring()` directly on this content, including in the *primary* semantic-search path's snippet generation, not just a fallback. Encryption required restructuring those call sites to fetch a bounded candidate set and operate on decrypted content in Python instead, since Neo4j can't do string operations on ciphertext (`375e446`).

    Suite after item 21 (both parts): 211 passed / 5 pre-existing-unrelated failed / 1 skipped, verified via the literal CI invocation on a clean-room `uv sync --frozen` rebuild (211/0/1).

---

## 5. Live end-to-end verification

Everything above was validated by a mocked test suite. This section is deliberately separate, because a mocked suite proved insufficient on its own — it required a real pass with actual API endpoints, live Neo4j/Redis, and real Gemini calls to find what it structurally could not.

### The mocking audit came first, because the "everything's mocked" assumption was false

Before running anything live, an audit of `backend/tests/` (`bafe79d`) found two files silently riding on pytest's collection order rather than genuine isolation:

- `test_pattern_integration.py` had **no `Neo4jGraph` patching at all**. `TestChainOfThoughtAgent` transitively constructed a real `Neo4jGraph()` (via `ChainOfThoughtAgent._risk_assessment_chain`'s lazy `PolicyRepository` import), and `TestReACTAgent` made a **real, unmocked `ChatGoogleGenerativeAI().invoke()` call** whenever `GOOGLE_API_KEY` was set — failures were swallowed, so the test "passed" regardless, but the call was genuinely made. Both only worked because an earlier-alphabetical test file's mock happened to still be cached in `sys.modules`; run either class in isolation and it hit real infrastructure.
- `test_rbac_integrated.py` had zero mocking at all (`from backend.main import app` transitively builds a real `Neo4jGraph()` the same way).
- `backend/shared/cache/redis_cache.py`'s module-level `cache = RedisCache()` singleton always attempted a real `redis.from_url(...).ping()` on import; whether a test got the `InMemoryCache` fallback was environmental luck, not a deliberate choice.

Fixed by wrapping both files' imports in the standard `Neo4jGraph` patch (with `.query.return_value = []` explicitly configured — a bare mock's `.query()` isn't iterable, which would make `PolicyRepository.get_applicable_policies` re-raise and silently flip assertions), injecting a fake LLM into `ReACTAgent.clause_tool`, and adding `backend/tests/conftest.py` to patch `redis.from_url` at module scope for the whole suite. Verified both files pass in isolation with `GOOGLE_API_KEY` unset and `NEO4J_URI` unreachable — proving the collection-order reliance was actually gone.

### What the live walkthrough confirmed working

Against real Docker-composed Neo4j/Redis and the real Gemini API (no mocking in the request path):

- **Real encryption, genuine ciphertext.** A direct `cypher-shell` query of `c.full_text` on a live-uploaded contract returned base64-looking AES-256-GCM ciphertext — confirmed unreadable, not a mocked assertion.
- **Real caching preventing redundant calls.** Re-running analysis on the same contract showed `total_calls` incrementing with genuine `cache_hits` for the clauses that had succeeded the first time (0 new real Gemini calls for those), while the 2 clauses that had failed a real 429 on the first run — correctly *not* cached, since a failed call never reaches the cache-set step — made fresh real calls on the second run and succeeded.
- **Real cross-tenant isolation.** The same contract ID requested with a different tenant's `tenant_id`, on both `GET /status` and `POST /analyze`, returned a clean 404 on each — not a mocked assumption.

### The 4 real bugs the live walkthrough found that a 211-test, all-passing suite did not

1. **`PolicyEvaluationService.evaluate_clause` silently swallowing real failures into a false clean result.** A genuine `429 RESOURCE_EXHAUSTED` during a live analysis run produced `intelligence_status: "completed"`, `risk_level: "LOW"`, `violations_count: 0`, with a `"success"` audit entry — because `evaluate_clause`'s `except Exception: return []` was indistinguishable, to every caller upstream, from "the model genuinely found zero violations." Fixed (`d163822`) by making `evaluate_clause` raise instead of swallow, `PolicyCheckerTool._run` catch per-clause and track `failed_clause_ids`, and both orchestration paths propagate an honest `"partial"` node status through to `processing_complete`/the persisted `intelligence_status` (now `"completed_with_errors"` when appropriate).
2. **Two separate Neo4j `DateTime` serialization leaks**, beyond the one initially found in `AuditLogger.get_audit_trail`: `GET .../status`'s `last_updated` field (`contract_intelligence.py`) and `ErrorTracker.get_recent_errors`'s `timestamp` field (reachable via `GET /api/audit/errors/recent`) both returned a raw `neo4j.time.DateTime` object, which FastAPI's default JSON encoder can't serialize and instead dumps as nonsensical `__dict__` internals (`"_Date__day": -2`). Found via a broad, deliberate search (not just the two flagged instances) and fixed with one shared helper (`serialize_neo4j_datetime`, `shared/utils/utils.py`) applied consistently at all three call sites, rather than a third inline copy of the same check. `18f84ab`.
3. **Unbounded LLM timeout/retry behavior.** An overnight benchmark run hung for **~3 hours** on a single clause-extraction call before finally raising "The read operation timed out" — no 429, no quota message. Investigation found `get_default_llm()` already had `request_timeout=120` configured; empirically proved (via a real socket that accepts a connection and never responds) that this timeout genuinely fires within its configured window when the process stays running. The likeliest explanation is the machine/session suspending mid-request — no in-process timeout mechanism, of any kind, can fire *during* a full process suspension, since no code executes at all until the OS resumes it. Closed the remaining real gap found along the way regardless: `llm_manager.py` and both `document_processing_service.py` files had no timeout/retry cap at all, unlike `get_default_llm()`. `35574b3`.

**The structural point**: a mock that always returns a canned successful LLM response cannot, by construction, distinguish "the policy check ran and genuinely found nothing" from "the policy check never actually ran." This is not a coverage gap that more mocked tests would have closed — it's a category of bug that is invisible to any test where the LLM call itself is stubbed out, because the entire bug *is* what happens when a real call fails. That is why this needed a live pass with a real, sometimes-failing API in the loop, not a broader mocked suite.

---

## 6. Honest current-state assessment

Deliberately scoped-out or still-partial items a reader should know about — not forgotten, consciously not done, or found and left for a future pass:

- **`tenant_id` still has no real authenticated-identity layer.** P1 item 6 scoped down to "reject if missing" (`ae7d400`) rather than the full fix ("derive from an authenticated claim, reject if missing *or mismatched*"). Any caller can still supply any `tenant_id` string it wants; there is no session/JWT verification that the caller is actually entitled to that tenant.
- **`ChainOfThoughtAgent`/ReACT "reasoning" is still deterministic f-string templating, not real LLM reasoning**, wherever it runs at all. `39bf98c` fixed a guaranteed crash (`NameError`), not the underlying nature of the output — and the default execution path (`PlanExecutionEngine`) still has no pattern-analysis/ReACT/CoT step type at all, so this code only runs on the traditional-workflow fallback.
- **2 known-broken async smoke-test files remain excluded from CI** (`test_ai_patterns.py`, `test_policy_system.py` — confirmed the exact current `--ignore` list, not a stale count), tracked explicitly as punch-list item 22 rather than silently dropped. Both are `async def` scripts with zero `assert` statements, calling real Agent classes with no LLM mocking — enabling `asyncio_mode=auto` to collect them would trade a deterministic collection failure for a live-API-dependent one against CI's fake key.
- **Risk-relevant clause extraction recall is meaningfully lower than metadata extraction** — the single most important accuracy finding of this engagement (§4 item 12): 0.32 average F1 across 36 risk-relevant categories vs 0.75 across the 5 metadata columns, full 497-contract scale. This is measured now, not hidden, but it is not fixed — the platform's core risk-flagging value proposition still has real, quantified accuracy gaps, especially in 20 of 36 categories scoring below 0.30 F1.
- **`convert_neo4j_date`'s zero-padding bug** (`shared/utils/utils.py:7-13`) — found during this session's broad Neo4j-DateTime-leak search, not fixed. Renders e.g. January 5th as `"2024-1-5"` instead of `"2024-01-05"`. Left out of scope deliberately: it's a different, milder bug class than the serialization crashes fixed in §5 (a malformed-but-present string, not a `__dict__` dump or a `TypeError`), and it only affects the `effective_date`/`end_date` fields already routed through it in the search-tool paths.
- **Three parallel orchestration systems still exist**, unconsolidated. Each was individually hardened where this engagement touched it (traditional workflow's `node_status`, `PlanExecutionEngine`'s `step_status`, Supervisor's now-correct-but-unused construction), but no decision was made to merge them or retire the disconnected Supervisor path.
- **`correlation_id`/`trace_id` bridging is a capability, not a wire-up.** All 4 MCP tool functions accept an optional `correlation_id` now, but nothing in this codebase currently calls from FastAPI into the MCP server in-process — there is no existing call chain to actually thread a live value through yet.
- **No real secrets manager.** `ENCRYPTION_KEY` is environment-variable-sourced with a loudly-logged insecure dev-only fallback if unset. `IKeyProvider` exists specifically so a real provider can be swapped in later without touching any encrypt/decrypt call site, but none is built, since no deployment target has been chosen yet.
- **`FieldEncryptor.decrypt()` falls back to returning its input unchanged on failure** — an explicit safety net for legacy plaintext during the migration window, not a security boundary. Don't rely on a decrypt failure to signal tampering.
- **The Chunk-content `CONTAINS`-fallback search path's `total_count` is a bounded approximation** (`CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT=500`), not a true unbounded count — the same tradeoff class already accepted for `VECTOR_SEARCH_OVERFETCH`, and it only affects a fallback path (the primary path is the real vector index).
- **PII detection is pattern-based** (the same `PIIEngine` used by the chat path), not a general/ML-based classifier — it covers a defined set of patterns, not every conceivable PII shape.
- **The gemini-2.5-flash risk-category confirmation sample is incomplete** (5/90 as of this report), gated on the free-tier per-minute/per-day quota. The daily recurring job continues to make incremental progress; this is a data-completeness gap, not an unresolved bug.

---

## 7. If continuing this work

In rough priority order, given everything found above:

1. **Complete the gemini-2.5-flash risk-category confirmation sample** (currently 5/90) once quota allows, to know whether the production model meaningfully closes the risk-category recall gap the way it did for metadata (§3).
2. **Build a real authenticated-identity layer** deriving `tenant_id` from a verified session/JWT claim, rejecting a mismatch as well as an omission — the structural fix underlying nearly every tenant-isolation finding in this whole engagement.
3. **Decide the fate of the 3 parallel orchestration systems.** Either wire the Supervisor's quality-gate/circuit-breaker machinery into the actual default (`PlanExecutionEngine`) path, or remove it — leaving working-but-disconnected machinery in place indefinitely is itself a maintenance liability.
4. **Convert or delete the 2 excluded async smoke-test files** using the `make_fake_llm`/`FakeGraph` mocking patterns already established across the rest of this suite (item 22).
5. **Extend real LLM-backed reasoning** to `ChainOfThoughtAgent`/`ReACT` if that pattern is worth keeping at all — currently it's deterministic templating dressed up as reasoning.
6. **Stand up a real secrets-manager-backed key provider** once a deployment target is chosen (`IKeyProvider` is already the extension point).
7. **Fix `convert_neo4j_date`'s zero-padding bug** — small, low-risk, already located.
8. **Wire `correlation_id` end-to-end** once a real FastAPI → MCP in-process call path exists to carry it.
9. **Push the risk-category benchmark further**: investigate the 20-of-36 categories scoring below 0.30 F1 individually — some (e.g. Price/Volume Restriction, Joint IP Ownership) may be near-zero due to genuine corpus rarity rather than model failure, which changes the remediation (more ground truth, not a better prompt).
