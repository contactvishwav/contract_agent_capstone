# Cross-tool working protocol

## Ownership and tool switching

1. One task has one active writer at a time.
2. One task uses one branch and one worktree.
3. Codex, Claude Code, Antigravity, and humans must not concurrently edit the same worktree.
4. A tool switch keeps the same task branch/worktree unless work is intentionally split with explicit file ownership and independent acceptance criteria.
5. The outgoing writer commits a coherent checkpoint when safe and updates the task contract. If a safe commit is impossible, it records every uncommitted file and why.
6. The incoming writer first performs a read-only audit of `AGENTS.md`, the task contract, Git status/diff, recent commits, relevant ADRs, and current checks.
7. Architectural disagreement is recorded in an ADR or PR discussion. No silent reversal.
8. Chat transcripts, summaries, and model memory are not authoritative.
9. Generated clients/schema, migrations, prompt versions, and their sources move together.
10. Passing local tests does not authorize or prove a production deployment.

## Task states

- **Implemented and verified:** acceptance behavior is evidenced by appropriate automated or runtime checks.
- **Implemented, not end-to-end verified:** code and focused tests exist, but the real integrated journey was not observed.
- **Documented, unverified:** a claim exists without current code/runtime confirmation.
- **Known defect:** reproducible or directly evident broken behavior.
- **Known limitation:** deliberate current constraint, including accuracy/resource limits.
- **Roadmap:** desired future capability with no claim of implementation.
- **Rejected/deferred:** evaluated and deliberately not selected; rationale should be an ADR candidate or accepted ADR.

## Standard prompts

### Start of session

> Read `AGENTS.md`, the active task contract, relevant architecture documents/ADRs, and repository status/diff. Do not edit yet. Summarize the goal, acceptance criteria, affected invariants, current branch state, existing changes, tests to run, and your first proposed action. Treat chat context as non-authoritative.

### End of session

> Run the task's declared verification. Update the handoff with completed work, remaining work, decisions, risks, changed files, exact failing checks, checks not run, uncommitted files, and the last commit. Do not claim success for anything not verified.

## Required handoff

Use [`tasks/TEMPLATE.md`](tasks/TEMPLATE.md). Include actual provider/model and default/fallback path evidence when either can change, plus tenant/security, migration, evaluation, and production impact where applicable.
