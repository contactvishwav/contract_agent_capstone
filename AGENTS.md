# Repository operating instructions

This file is authoritative for every coding agent. Chat transcripts and model memory are not sources of truth.

## Read before editing

1. The active task contract under `docs/tasks/` (if any), then `git status`, staged/unstaged diffs, the current branch/base, and recent commits.
2. `docs/SOURCE_OF_TRUTH.md` and the documents it assigns to the task area.
3. Relevant ADRs under `docs/adr/`; `docs/SYSTEM_MAP.md` for verified paths and known gaps.
4. Relevant tests, migrations, API types, generated artifacts, and feature code.

If instructions conflict, use: validated runtime/code and tests > accepted ADRs > current architecture/security/evaluation/deployment docs > historical summaries > chat. Stop and record unresolved architectural conflict; do not silently reverse a decision.

## Non-negotiable invariants

- Tenant identity comes only from validated authentication context. Never trust a tenant value from request bodies, query parameters, prompts, tool arguments, task payloads without authenticated server-side derivation, or external MCP callers.
- Scope every Neo4j read/write and Redis data/cache key by authenticated tenant where data is tenant-owned. Relationships are not a substitute for tenant predicates.
- Propagate tenant, correlation, idempotency, model/prompt version, and execution-path identity across HTTP, Celery, Neo4j, Redis, MCP, and audit boundaries. Never use `default-tenant`/`demo_tenant_1` in a production-reachable path.
- `PlanExecutionEngine` is the default analysis path; LangGraph traditional analysis is fallback. A plausible result does not prove which path ran. Preserve and test explicit path identity—never hide fallback execution.
- Contract scope and chat-session identity are separate. A session's persisted contract scope is server-authoritative once the session exists.
- Contract Chat messages must remain provider-neutral at the persistence boundary; preserve role, content, ordering, actual provider/model attribution, tool-call identity, and safe replay semantics.
- Preserve grounding, citations, source traceability, output guards, auditability, and human-review framing. Current risk-category accuracy does not support autonomous legal decisions.
- The REST and tool-facing enhanced-search implementations intentionally differ in thresholds, capabilities, caching, and return contracts. Do not consolidate them as mechanical cleanup.
- Celery tasks must carry authenticated tenant scope, be idempotent or explicitly documented otherwise, and report partial/failure states honestly. Do not treat enqueue success as analysis success.
- Keep API clients/types, migrations/schema, prompt versions/source hashes, and generated artifacts synchronized with their sources.
- Never log raw contracts, prompts, tokens, secrets, decrypted sensitive fields, or unrestricted tool results. Audit records must carry the real tenant and trace ID.
- Never commit `.env` files or secret values. Never deploy, clean databases, or assume local resource behavior fits the GCP e2-micro production target without an explicit task and release verification.

## Working rules

- One task has one active writer, branch, and worktree. Different tools must not edit the same worktree concurrently. Tool switches retain the task branch/worktree unless the task is intentionally split.
- Inspect and classify existing changes before editing. Preserve unrelated work; never reset, clean, stash, reformat, rewrite, squash, or amend another tool's work merely for convenience.
- Make the smallest coherent change. Do not weaken tests, security, tenancy, type checks, error reporting, or evaluation criteria to obtain a green check.
- Run task-declared verification plus focused tests for affected invariants. Report exact failures and every relevant check not run. Local tests never authorize production deployment.
- When safe, the outgoing tool commits a coherent checkpoint and updates the task handoff. The incoming tool begins with a read-only audit of this file, task contract, status/diff, commits, ADRs, and checks.
- Architectural disagreement belongs in an ADR or PR discussion, not an undocumented implementation reversal.

## Handoff minimum

Update the task contract with owner/tool, branch/base/last commit, decisions, affected invariants, completed and remaining work, changed and uncommitted files, exact passing/failing/not-run checks, risks, and next action. See `docs/tasks/TEMPLATE.md` and `docs/WORKING_PROTOCOL.md`.
