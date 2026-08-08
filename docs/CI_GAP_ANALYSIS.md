# CI gate audit and recommendation

Current workflow evidence: `.github/workflows/ci.yml` provides a real Neo4j 5.26 service, runs the backend suite, blocks Ruff `E9,F821`, reports full Ruff output, and runs report-only `pip-audit`/`npm audit`. It does not run Redis, a Celery worker, frontend lint/build, browser tests, migration execution, OpenAPI drift, or deployment verification.

| Gate | Current state | Recommended tier |
|---|---|---|
| Backend tests with real Neo4j | Present, blocking | **Blocking now**; remove collection-order/import coupling over time |
| Python syntax/undefined names | Present, blocking | **Blocking now** |
| Persistent-session focused tests | Collected by backend suite; largely mocked repositories | **Blocking now** plus future real Neo4j integration |
| Frontend TypeScript/build | Local script exists, absent from CI; current build passes with one CSS minifier warning | **Blocking now** after recording/triaging that warning |
| Frontend ESLint | Script exists, absent from CI; 2026-08-08 baseline is 32 errors and 7 warnings, including pre-existing and session-slice findings | **Report-only** until triaged, then blocking |
| Frontend component/unit tests | No test runner/script | **Report-only roadmap**, then blocking for state/session reducers and API adapters |
| Redis/Celery integration | Redis broker/result config exists; no CI service/worker journey | **Report-only initially**, then blocking for enqueue/status/tenant/idempotency contracts |
| Migrations/schema validation | Unit coverage; migrations not run against CI Neo4j | **Blocking now** for idempotent migration smoke on an empty disposable DB after baseline review; destructive legacy migrations require explicit safeguards |
| Neo4j tenant isolation | Many focused tests, mostly mocks/fake graphs | **Blocking now** for current tests; add a smaller real-database adversarial suite |
| Redis key tenant scoping | Code-specific tests, no systematic gate | **Blocking now** for touched cache paths; scheduled repository-wide audit |
| Execution-path identity | Internal planning regression test exists; API response drops `planned_execution` and hardcodes `phase_used` | **Blocking once fixed**; report-only finding until contract is designed |
| OpenAPI/client contract drift | Handwritten TypeScript clients/types | **Report-only now**; make blocking after schema generation/check-in policy is chosen |
| Prompt/source version tests | Extraction prompt source-hash/version test exists | **Blocking now** for affected prompt paths; extend to chat/system/reranker prompts |
| MCP tests | Unit/in-process tests present; standalone auth boundary unresolved | **Blocking now** for existing behavior; security design gate before external exposure |
| Model calls | Tests mock providers; live calls quota-dependent | **Blocking mocked tests**; **scheduled/nightly** recorded evaluations; **live/manual** credentialed smoke |
| Dependency/security scans | Present, report-only | Keep **report-only** until baseline triage; severe exploitable production findings become blocking by policy |
| SAST/secret scanning/SBOM | Not present | **Blocking now** for secret scanning; **report-only** SAST/SBOM until baseline triage |
| Browser end-to-end | Absent | **Blocking release gate** for upload→analysis and persistent chat; lightweight auth/navigation smoke per PR once stable |
| Production linux/amd64 build | Not in CI | **Release-only blocking** multi-stage image build and architecture inspection |
| Deployment smoke/rollback | Manual runbook only | **Release-only/live manual** with health, auth, tenant-negative tests, migration status, rollback evidence |
| Benchmark/evaluation | Offline artifacts and scripts | **Scheduled/manual**, never per-PR live quota; block claim changes on checked evidence |

Do not promote full Ruff, ESLint, dependency audit, or other debt-bearing reports to blocking until their exact baseline is recorded and triaged. CI success does not establish browser behavior, live-provider quality, migration safety on populated production data, or e2-micro resource fit.
