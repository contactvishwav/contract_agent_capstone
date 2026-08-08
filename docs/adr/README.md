# Architectural Decision Records

Accepted ADRs record real decisions prospectively. Do not retroactively manufacture consensus. Use [`TEMPLATE.md`](TEMPLATE.md), assign the next number, and keep status explicit (`Proposed`, `Accepted`, `Superseded`, `Rejected`).

## Accepted

1. [`ADR-001`](001-server-authoritative-chat-session-scope.md): persisted chat-session scope is server-authoritative.
2. [`ADR-002`](002-standalone-mcp-trust-boundary.md): standalone MCP is local-only until external authentication exists.

## Decision candidates (not yet accepted ADRs)

1. `PlanExecutionEngine` as default analysis and LangGraph as explicit fallback, including externally visible path identity.
2. Neo4j as both graph and native vector store.
3. Canonical provider-neutral persisted chat messages and actual model/provider attribution.
4. Small-document full-context versus hierarchical/chunk retrieval.
5. Permanent separation or deliberate convergence plan for the two enhanced-search implementations.
6. Model-provider fallback eligibility, ordering, quality/cost controls, and attribution.
7. Remaining tenant isolation boundaries across HTTP, Celery, Neo4j, Redis, audit, and local client state.
8. Attachment storage and lifecycle once requirements exist.

Candidates describe questions only. Current behavior remains governed by code, tests, `AGENTS.md`, and `docs/SOURCE_OF_TRUTH.md` until an ADR is accepted.
