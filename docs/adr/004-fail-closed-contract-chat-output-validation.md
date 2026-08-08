# ADR-004: Fail-closed Contract Chat output validation

- Status: Accepted
- Date: 2026-08-08
- Owners: architecture, security/tenancy, legal-data governance, and application roles
- Related task/PR: `docs/tasks/active/output-guard-repair.md`

## Context

The safety and hallucination validators requested a model through
`LLMManager.get_model_by_name()`. That lookup returns Contract Chat's compiled
LangGraph `MessagesState` graph, but the validators invoked it with a provider-
style string. LangGraph rejected the root state update with
`InvalidUpdateError` before a provider call. Both validators caught the error
and returned `is_safe=True`, so audit, persistence, and the browser represented
an unexecuted check as a pass.

Contract Chat handles sensitive legal evidence. A validator crash cannot be
treated as evidence that generated output is safe or grounded.

## Decision

Output validators call the server-configured raw provider model explicitly;
the compiled Contract Chat graph remains exclusive to the chat/tool workflow.
Output Guard runs its required validators sequentially and retains a bounded
result for each. Aggregation is deterministic: infrastructure failure outranks
policy rejection, and any non-pass prevents release. There are no parallel
state writes or reducers in Output Guard.

The policy for terminal outcomes is:

1. Successful validators and a passed answer: release, persist, and mark
   `passed`.
2. Successful validators rejecting unsafe or hallucinated content: withhold
   generated text, return a bounded message, persist `rejected`.
3. One or all validators failing: withhold generated text and persist
   `validation_failed`; another validator's pass cannot overwrite it.
4. Guard timeout: end the request as `timed_out`.
5. Client cancellation: propagate cancellation and best-effort persist
   `cancelled` for the already-persisted user turn.
6. Empty/malformed generation: withhold and represent it as `empty`; malformed
   validator responses are `validation_failed`.
7. Missing grounding evidence: reject contract-answer output as
   `UNGROUNDED_OUTPUT`; do not claim it passed a hallucination check.
8. Retrieved/tool evidence is untrusted data, not instructions. It is delimited
   as evidence in the grounding prompt.
9. Provider generation failure remains distinct as `generation_failed`.
10. Final persistence failure prevents answer release, ends SSE as
    `persistence_failed`, and emits only bounded diagnostics. If storage remains
    unavailable, the terminal record is necessarily best effort.

Generated assistant text is buffered until validation completes. SSE always
emits an explicit bounded error plus `end` for server-controlled rejection,
validation failure, timeout, generation failure, and persistence failure.
Disconnect cancellation cannot deliver an event to the disconnected client;
its durable terminal representation is restored from session history.

## Alternatives considered

- Continue fail-open behavior: rejected because a crashed check is not a pass.
- Return a visibly “unvalidated” legal answer: rejected without an accepted
  product/evaluation basis for exposing unvalidated legal content.
- Invoke the compiled graph with a correctly shaped message-state mapping:
  rejected because that would run the full chat/tool workflow recursively, not
  the intended provider classifier.
- Parallelize validators with reducers: rejected because the existing chain is
  small and sequential; explicit ordered results avoid reducer/version drift
  without creating a second LangGraph.
- Stream tokens before post-check: rejected because a later rejection cannot
  retract content already disclosed to the browser.

## Consequences

Safety and grounding failures are now truthful and restorable. The additive
`ChatMessage.terminal_status` property is nullable, so legacy rows remain
readable without a destructive migration; a missing legacy status is not
retroactively claimed as a verified pass.

Users receive the answer after validation rather than token-by-token before the
post-check. Existing safety and hallucination classifier calls still add model
latency and cost; this decision adds no new validator beyond that design. The
fixed 60-second guard deadline is configurable for deployment evaluation, but
local success does not prove suitability for the e2-micro production target.

## Verification and observability

Deterministic tests reproduce the old `InvalidUpdateError`, prove raw-model
invocation, exercise pass/reject/one-failure/all-failure/conflict/malformed/
missing-evidence/timeout/cancellation/persistence paths, and verify SSE and
restoration. Audit metadata includes bounded validator/status/model/session/
correlation fields and excludes prompts, generated output, contract evidence,
tool payloads, and exception messages. Prometheus counts bounded status and
reason-category labels.

## Rollout and rollback

No schema backfill is required. Deployments must verify provider configuration,
guard latency, terminal-outcome metrics, and a real grounded turn before release.
If validation becomes unavailable, disable Contract Chat or roll back to the
last known fail-closed implementation; do not restore the fail-open exception
path. Nothing in this ADR authorizes production deployment.
