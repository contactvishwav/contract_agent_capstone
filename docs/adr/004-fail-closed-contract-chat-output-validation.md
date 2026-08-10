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

## Addendum (found live, independent audit, 2026-08-09): Prompt Guard's
## `TopicValidator` deny-list is a cost optimization, not a safety control

An audit pass flagged an apparent non-determinism in `PromptGuard`: the same
off-topic request ("a cookie recipe") was rejected pre-Guard on one run and
reached the model on another. Direct, repeated invocation of
`TopicValidator.validate()` in isolation proved it is fully deterministic -
pure Python substring matching against a static `OFF_TOPIC_PHRASES` list, zero
randomness, zero model call. The two runs differed only in exact phrasing:
"recipe for chocolate chip cookies" contains the deny-listed phrase
`"recipe for"`; "chocolate chip cookie recipe" does not. This is a real, if
mundane, phrase-matching gap in a deny-list that (per `topic.py`'s own
docstring) already accepts it cannot enumerate every paraphrasing of an
off-topic request - not a reliability defect in the Guard chain.

That gap matters only for what it implies about the chain's cost-control
story. `PromptGuard`'s stated purpose for `TopicValidator` is catching
obviously off-topic prompts *cheaply*, before any retrieval or model spend.
When a phrasing slips past the deny-list, the request does not become unsafe:
it proceeds through `IntentValidator` (scoped to malicious/security-bypass
intent only, not off-topic small talk, so it correctly also does not catch
this class of request) and then to the real agent and Output Guard, whose
fail-closed grounding check (`UNGROUNDED_OUTPUT`, item 7 above) rejects it
regardless, because an off-topic request has no supporting contract evidence
to ground an answer in. Both paths already fail safe; the only real difference
is that a deny-list miss costs one extra retrieval-and-generation cycle
instead of a free, immediate rejection.

**Decision: do not expand `OFF_TOPIC_PHRASES` to chase this.** A deny-list is
inherently brittle against arbitrary paraphrasing - closing "cookie recipe"
today only leaves the next unlisted phrasing open tomorrow, for marginal,
unbounded engineering cost chasing a cost-optimization path whose safety
outcome does not depend on it. Output Guard's grounding check is, and remains,
the actual backstop for off-topic requests; `TopicValidator` is intentionally
scoped as a best-effort latency/cost shortcut in front of it, not a second
safety gate. If off-topic-request cost becomes a measured problem (not just a
theoretical one), the correct fix is a cheap classifier with real recall
guarantees, not a growing ad hoc phrase list.

## Addendum (2026-08-10): a new terminal outcome, `generation_timeout`

Reconciliation-audit finding: `runner()`'s astream consumption loop (the
generation phase itself, upstream of everything this ADR's terminal-outcome
policy already covers) had no timeout at all - a hung/stalled provider
stream could hold the request open indefinitely, unlike every other
provider-facing call elsewhere in this engagement (`EXTRACTION_TIMEOUT_
SECONDS`, `RERANKER_TIMEOUT_SECONDS`, and this ADR's own `OUTPUT_GUARD_
TIMEOUT_SECONDS`).

Bounded with `GENERATION_STALL_TIMEOUT_SECONDS` (default 60s, same order of
magnitude as item 4's guard deadline) - deliberately a per-chunk **idle**
timeout, not a total-duration cap on the whole turn: this phase streams
`tool_call`/`tool_message`/`user_message` events live to the client as they
arrive (item captured in "Users receive the answer after validation..."
under Consequences), so a total-duration wrapper would force buffering all
of it, defeating that live behavior, and would incorrectly kill a slow-but-
still-progressing multi-tool-call turn. An idle timeout only trips once the
provider has gone genuinely silent - which is what "hung" actually means.

New terminal outcome, `generation_timeout`, added alongside item 4's
`timed_out` (which remains Output Guard's own, distinct outcome - it never
overlaps with this one, since a stalled generation never reaches Output
Guard at all): withholds any partial content, persists and audits the
terminal record the same way every other mid-stream failure already does
(`resilient_runner`'s existing catch-all path - reused as-is, not
duplicated), and ends the SSE stream with a bounded "Response generation
timed out. Please retry." message.
