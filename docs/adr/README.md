# Architectural Decision Records

Accepted ADRs record real decisions prospectively. Do not retroactively manufacture consensus. Use [`TEMPLATE.md`](TEMPLATE.md), assign the next number, and keep status explicit (`Proposed`, `Accepted`, `Superseded`, `Rejected`).

## Accepted

1. [`ADR-001`](001-server-authoritative-chat-session-scope.md): persisted chat-session scope is server-authoritative.
2. [`ADR-002`](002-standalone-mcp-trust-boundary.md): standalone MCP is local-only until external authentication exists.
3. [`ADR-003`](003-contract-archive-and-replacement-lifecycle.md): contract removal is tenant-scoped soft archive with active-content duplicate identity.
4. [`ADR-004`](004-fail-closed-contract-chat-output-validation.md): Contract Chat output validation uses the raw model boundary and fails closed with explicit terminal outcomes.
5. [`ADR-005`](005-encrypted-source-pdf-provenance-and-viewer.md): source PDFs remain encrypted and citations use authenticated, truthful locator tiers.
6. [`ADR-006`](006-server-model-registry-and-explicit-legal-failure.md): the server model registry is authoritative and legal-workflow provider failure is explicit.
7. [`ADR-007`](007-canonical-contract-chat-evidence-envelope.md): generation, grounding, and citations share one tenant-authorized evidence envelope and claim/evidence policy.
8. [`ADR-008`](008-contract-chat-attachments-and-quote-reply.md): Contract Chat image attachments use encrypted, session-scoped storage and a provider-agnostic content-block format; quote-reply carries a bounded excerpt with non-regrounding citation re-surfacing.

## Decision candidates (not yet accepted ADRs)

1. `PlanExecutionEngine` as default analysis and LangGraph as explicit fallback, including externally visible path identity.
2. Neo4j as both graph and native vector store.
3. Small-document full-context versus hierarchical/chunk retrieval.
4. Permanent separation or deliberate convergence plan for the two enhanced-search implementations.
5. Remaining tenant isolation boundaries across HTTP, Celery, Neo4j, Redis, audit, and local client state.

Candidates describe questions only. Current behavior remains governed by code, tests, `AGENTS.md`, and `docs/SOURCE_OF_TRUTH.md` until an ADR is accepted.
