# Architectural Decision Records

Accepted ADRs record real decisions prospectively. Do not retroactively manufacture consensus. Use [`TEMPLATE.md`](TEMPLATE.md), assign the next number, and keep status explicit (`Proposed`, `Accepted`, `Superseded`, `Rejected`).

## Decision candidates (not yet accepted ADRs)

1. `PlanExecutionEngine` as default analysis and LangGraph as explicit fallback, including externally visible path identity.
2. Neo4j as both graph and native vector store.
3. Canonical provider-neutral persisted chat messages and actual model/provider attribution.
4. Contract scope versus chat-session identity and server-authoritative scope rules.
5. Small-document full-context versus hierarchical/chunk retrieval.
6. Permanent separation or deliberate convergence plan for the two enhanced-search implementations.
7. Model-provider fallback eligibility, ordering, quality/cost controls, and attribution.
8. Tenant isolation boundaries across HTTP, Celery, Neo4j, Redis, MCP, audit, and local client state.
9. Attachment storage and lifecycle once requirements exist.

Candidates describe questions only. Current behavior remains governed by code, tests, `AGENTS.md`, and `docs/SOURCE_OF_TRUTH.md` until an ADR is accepted.
