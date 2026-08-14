# Output Guard evidence lifecycle repair

- Status: Implementation and local acceptance complete; documentation checkpoint blocked
- Active owner/tool: Codex 5.6 Sol
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `fa4e05a8fba53d8596e670d819de418f9c685298`
- Last known commit: `e137a9b` (tests; documentation/handoff commit follows)

## Goal

Repair false rejection of ordinary grounded Contract Chat answers by preserving
tenant-authorized, provider-neutral structured evidence from tool execution
through generation, citation, fail-closed Output Guard validation, persistence,
SSE, and restoration.

## Non-goals

- Production deployment or push
- Broad RAG/search redesign, second answer-rewrite call, or reduced grounding
- Multi-model switching, attachments, production readiness, or database cleanup
- Changes to grouped-extraction research artifacts

## Acceptance status

- [x] Reproduced every declared answer class with authenticated Gemini 2.5 Flash.
- [x] Root-caused the no-tool/current-evidence mismatch before changing prompts.
- [x] Generation, grounding, and citations share canonical current-turn evidence IDs.
- [x] Metadata/count, text, and per-contract comparison evidence validate correctly.
- [x] Unsupported, contradicted, fabricated, archived, cross-tenant, and empty
  evidence remain fail closed.
- [x] Prompt Guard, unsupported output, no evidence, validator failure, timeout,
  and cancellation have distinct bounded messages/reason categories.
- [x] PDF navigation, refresh restoration, provider attribution, and cancellation
  passed real browser acceptance.
- [x] Focused backend/frontend checks, build, lint, Ruff, and full suites pass.
- [ ] Implementation and tests are committed separately. The documentation
  checkpoint was blocked before staging by the environment's approval/usage
  limit; nothing was pushed or deployed.

## Invariants affected

- Authenticated tenant identity and active-contract authorization
- Grounding, citations, source traceability, auditability, and human review
- Provider-neutral persistence, prompt version, and actual model attribution
- SSE/API/frontend terminal-state synchronization
- MCP/tool data-versus-instruction boundary and sensitive-data logging
- Search-path separation, production constraints, and secret handling

## Root cause and evidence-lifecycle findings

The reported catalog and broad prompts were generated without any tool call.
Output Guard correctly saw no current-turn evidence and rejected them as
`UNGROUNDED_OUTPUT`; the user-facing message was too generic. For turns that did
use tools, generation received raw heterogeneous `ToolMessage` payloads, while
the runner later concatenated a separately reparsed string for grounding and
built citations only after validation. That boundary lost evidence class,
stable identity, contract grouping, deterministic counts, and locator semantics.

Live verification exposed two additional shape/trust gaps and added regressions:

- Enhanced all-level search nests Neo4j rows under per-level `result` wrappers;
  the first normalizer draft classified wrappers as records and lost IDs/text.
- Gemini selected legacy `ContractSearch` with model-authored raw Cypher for a
  count. Contract Chat now routes metadata intents deterministically and never
  forwards that hook; the standalone search implementation is unchanged.

## Decisions

- ADR-007 defines canonical `chat-evidence-v1` and the claim/evidence policy.
- Stable evidence IDs bind tenant, source class, contract, locator, excerpt, and
  facts. Active tenant ownership is rechecked before contract evidence enters.
- Metadata and deterministic aggregation may ground list/count/type/party facts
  without pages. Textual legal/commercial claims require text evidence; cross-
  contract claims require evidence from each contract.
- Tool payloads remain untrusted data. Unknown/recomputed IDs, wrong counts,
  invented filenames, archived/cross-tenant evidence, and prompt-like content
  fail deterministically before semantic validation.
- The remaining semantic validator uses strict JSON. Infrastructure/malformed
  responses never pass. No second answer-rewrite call was added.
- `contract-chat-v2-evidence` is the prompt version. Comparison intent takes
  precedence over overlapping catalog words.

## Work completed

- Added canonical evidence normalization/combination, real nested-result support,
  bounded MCP status, tenant/active reauthorization, safe evidence observability,
  and deterministic metadata rendering.
- Wired the exact envelope into generation, Output Guard, citation creation,
  encrypted persistence, restoration, SSE, and frontend types.
- Added strict grounding schema and deterministic identity, tenant, active-state,
  count, filename, and text-versus-metadata checks.
- Added safe terminal reason persistence and distinct messages.
- Clarified that authorized contract summaries/comparisons with human-review
  framing are not automatically classified as prohibited legal determinations.
- Closed model-authored raw-Cypher forwarding at the Contract Chat boundary.
- Removed import-time Neo4j initialization from the evidence module after the
  full suite exposed test/runtime coupling.

## Browser acceptance (Gemini 2.5 Flash)

- `Clean_MSA.pdf` payment: passed; substantive 90-day answer; two sources; page 1
  opened and showed the cited payment text; refresh restored answer/citations.
- `Clean_MSA.pdf` termination: passed with page-1 citation.
- Available contracts: passed; exactly `Clean_MSA.pdf` and `Clean_SOW.pdf` with
  truthful metadata provenance and no fabricated page.
- Count: passed; exactly 2 active contracts.
- Focused comparison: passed with two tool calls, substantive payment/termination
  comparison, both contracts represented, and both page-1 PDFs verified.
- Exact broad reported prompt: passed with one all-level tool call, a detailed
  structured report, 10 sources, both contract pages, and Flash attribution.
- Unsupported annual-training-hours fact: qualified that the fact was absent and
  cited the searched contract rather than inventing a value.
- Cookie recipe: Prompt Guard `out_of_scope` message was distinct from evidence failure.
- Cancellation: safe broad request persisted `cancelled/client_cancellation`;
  `Generation stopped` remained after 18 seconds with no late answer/model result.

## Verification outcomes

- Comprehensive focused backend: `199 passed`.
- Final full backend: `846 passed, 1 skipped`.
- Focused frontend chat/PDF/session/cancellation: `18 passed`.
- Full frontend: `29 passed`.
- Frontend production build: passed; existing CSS minifier and large-chunk warnings.
- Touched frontend ESLint: 0 errors, 1 existing Fast Refresh warning.
- Touched Python Ruff: passed; blocking `E9,F821` Ruff: passed.
- Python compileall: passed.
- Real backend health: cache, Neo4j, and performance healthy on local port 8001.

## Failing checks

None.

## Blocking condition

The requested documentation commit was rejected before command execution because
the environment reported its approval/usage limit. The five task-owned Markdown
files below remain unstaged. No retry or indirect Git write was attempted.

## Checks not run

- No production deployment, push, production smoke/rollback, e2-micro capacity,
  cross-provider live matrix, OCR, destructive cleanup, or external MCP transport.
- No live validator exception/timeout was induced with paid credentials; these
  paths are deterministic/mocked tests. Live calls were limited to Flash acceptance.

## Commits

- `8f422f3` — `fix(chat): preserve structured evidence through output validation`
- `e137a9b` — `test(chat): cover grounded metadata and cross-contract answers`
- Documentation/ADR/handoff commit remains blocked and uncreated.

## Changed/uncommitted files

- Documentation pending in this handoff: ADR-007, ADR index, source-of-truth map,
  system map, and this task record.
- Protected, pre-existing, untouched, untracked:
  `research/benchmark/pilot_grouped_extraction.py` and
  `research/benchmark/pilot_grouped_extraction_sample.json`.

## Risks/questions

- The exact broad response begins with an unnecessary capability disclaimer before
  delivering the report. It passed grounding and is a response-quality issue, not
  a validation or release blocker for this repair.
- Local stack warns that JWT/encryption dev fallback keys are active. Do not use
  this runtime with real legal data or infer production readiness.
- Validator calls add latency/cost; e2-micro fit and provider availability remain
  release-only work. Actual provider attribution is not independent attestation.
- Source lists may include multiple evidence items per contract; compact citation
  ranking/deduplication is a future UX task, not a grounding defect.

## Recommended next action

Create the pending documentation checkpoint once Git-write approval is available;
then manual citation/model testing can resume. The next release-oriented task should
triage provider/runtime blockers and production resource/security readiness without
weakening ADR-004/007 or deploying from this local verification alone.
