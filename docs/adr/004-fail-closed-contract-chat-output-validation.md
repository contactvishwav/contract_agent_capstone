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

## Addendum (2026-08-10): `image_attachment` grounding evidence

Reconciliation finding from ADR-008 (Contract Chat image attachments):
a genuine, correct vision answer describing an attached image was
rejected by `HallucinationValidator` as `insufficient_scope` - the
evidence envelope had nothing representing the image the responding
model actually examined, so the LLM auditor correctly (from its own
narrow perspective) found the claim unsupported. Confirmed live: a direct
raw-provider call bypassing the graph/Output Guard entirely proved the
underlying vision answer itself was accurate.

Fixed narrowly, not by relaxing grounding generally. A new evidence
`source_type`, `image_attachment` (`chat_evidence_service.py`'s
`image_attachment_evidence_item()`), represents an attached image the
responding model directly examined in this turn - `contract_id` is always
`None` (it isn't contract-owned content), and `verification_status` is
`tenant_authoritative`, matching `deterministic_aggregation`'s existing
pattern for evidence with no contract owner. `runner()` appends one such
item per successfully-loaded attachment to the combined envelope before
Output Guard runs.

This new source type is deliberately **not** one of the types
`HallucinationValidator`'s deterministic legal-terms check treats as
satisfying `text_source_present` - a turn that mixes an attached image
with a legal/contract-content claim still requires real
document_text/section/clause/chunk evidence for that claim, exactly as
before this addendum; only image-describing claims are newly grounded.
The LLM auditor's own prompt gained one guideline explaining this
distinction explicitly, so the same narrow scoping holds at the
LLM-judged layer, not just the deterministic pre-checks.

Because `contract_id` is always `None`, `chat_citation_service.py`'s
citation builder (which requires a `contract_id`) automatically skips
this evidence type - no bogus "citation" is ever fabricated for an
attached image, the same existing behavior `deterministic_aggregation`
evidence already relies on.

## Addendum (2026-08-11): bounded retry for `HallucinationValidator`'s audit step

While investigating a separate multi-turn image-context issue, repeated
live calls surfaced a real, distinct problem: the exact same candidate
answer plus the exact same evidence envelope, resubmitted unchanged to
`HallucinationValidator`'s audit prompt, sometimes came back `"supported"`
and sometimes `"unsupported"`/`"contradicted"` across independent real API
calls. `temperature=0` is set on the auditor's model construction
(`llm_manager.py`), but that does not guarantee determinism on hosted
multi-tenant LLM APIs - a documented limitation, not a bug in this
codebase. Measured directly: ~20% single-call false-rejection rate on
genuinely correct, fully-evidenced answers, most visibly on single-turn
"what's in this image?" questions with no history at all.

This is auditor measurement noise, not an evidence-sufficiency problem -
the fix must absorb the noise without touching what counts as sufficient
evidence or weakening the fail-closed guarantee itself.

**Design.** `HallucinationValidator.validate()`/`avalidate()` retry only
the audit judgment call - the same `_prompt(input_text, evidence_envelope)`
built from the exact same candidate and evidence already fixed by the
caller - up to `MAX_AUDIT_ATTEMPTS = 2` total attempts (1 initial + 1
retry), stopping at the first passing verdict. Nothing upstream of the
audit call is retried: the generating model is never re-invoked, no new
candidate answer is ever produced, and `_validate_envelope`'s deterministic
pre-checks (missing evidence, fabricated evidence IDs, cross-tenant
evidence, missing text evidence for legal terms, etc.) run once, before the
retry loop, exactly as before - those are pure logic, not LLM judgment,
and have no flip-flop to absorb. An infrastructure failure (a raised
exception - malformed JSON, provider error, or a per-attempt timeout - see
below) is a different failure mode from a verdict flip-flop and is
deliberately not retried by this mechanism; it still fails closed on the
first occurrence via `_failure_result`, unchanged.

If every attempt rejects, the turn still fails closed exactly as before -
no change to that guarantee. This reads as "the auditor's verdict was
inconsistent, ask again," never "keep trying until we get a passing
answer": the question and its inputs are fixed throughout, only the
auditor's own independent judgment call is repeated.

**Why 2 total attempts (revised down from 3).** The first version of this
mechanism used `MAX_AUDIT_ATTEMPTS = 3` (1 initial + 2 retries), modeled
directly from the measured ~20% single-call failure rate: if each
independent attempt is ~80% likely to correctly recognize a genuinely
supported claim, the chance all 3 independently misjudge it is roughly
`0.2^3 ≈ 0.8%`, i.e. a combined ~99%+ pass rate. That version was live
load-tested and found to produce a worst-case audit latency of
~45-55 seconds on its own - a real, measured contributor to a live request
that hung well past every other timeout in the system (see the
`generation_timeout` addendum above; the hang itself turned out to be a
separate, unrelated bug in the SSE layer, but the audit retry's own
worst-case latency was still judged too costly relative to its marginal
benefit). Revised to `MAX_AUDIT_ATTEMPTS = 2`: at the same measured ~20%
single-call failure rate, 2 independent attempts' combined failure
probability is `0.2^2 = 4%`, i.e. a ~96% pass rate for a genuinely
supported claim - close enough to 3 attempts' ~99%+ that the additional
~15-20s of worst-case latency a third attempt adds was not judged worth
it, while cutting the mechanism's own worst-case latency contribution by
roughly a third.

**Per-attempt timeout.** The original design bounded only the *count* of
attempts, not any individual attempt's own duration - each attempt was
free to take however long the underlying provider call took, with the
outer `OUTPUT_GUARD_TIMEOUT_SECONDS` (60s, wrapping the entire Output
Guard chain, not just this one validator's retry loop) as the only
backstop. `AUDIT_ATTEMPT_TIMEOUT_SECONDS` (default `25`, env-overridable)
now bounds each individual audit call: real observed per-attempt audit
latency during live testing was ~15-20s, so 25s gives real margin
(~25-50%) above that observed ceiling before an attempt is treated as an
infrastructure failure rather than a slow response. With
`MAX_AUDIT_ATTEMPTS = 2`, two sequential attempts can now mathematically
reach at most 50s combined - leaving a real, enforced ~10s margin under
the 60s outer bound for the rest of the validator chain (`LlamaGuard`,
`DomainCompliance`, PII redaction), rather than a margin that existed only
by observed convention. A timed-out attempt is handled exactly like any
other raised exception from the audit call: an infrastructure failure, not
a verdict flip-flop, so it is not itself retried - it fails closed
immediately via `_failure_result`, same as before this addition.

**Visibility.** `audit_attempts` (1-2) and `audit_retry_used` (bool) are
attached to the result's metadata regardless of the outcome.
`base.py`'s `_aggregate_validator_results` explicitly preserves these two
keys from whichever validator set them, since the normal tie-break (first
validator wins when all pass) would otherwise silently drop this
visibility data on every passing turn that needed a retry -
`HallucinationValidator` runs last in Output Guard's chain and is rarely
the "chosen" result when everything passes. `AgentAuditService.
log_guard_check` persists both fields to the existing Neo4j-backed
`AuditLog` audit trail on every Output Guard check (pass or fail) -
queryable via the existing `GET /api/audit/trail/{resource_id}` route, not
just visible in transient request logs. Prompt Guard's own audit call site
is untouched and always logs these fields as `None`.

**Scope.** This retry lives entirely inside `HallucinationValidator`'s own
audit step. No other validator (`LlamaGuardValidator`,
`DomainComplianceValidator`), no other guard (`PromptGuard`), and no other
part of the generation pipeline gained retry logic as part of this change.

Live-verified: the exact 15-iteration single-turn "what's in this image?"
test used to diagnose the ~20% flip-flop rate was re-run against the fix -
see `docs/tasks/active/chat-attachments-and-quote-reply.md` for the before/
after pass-rate numbers.
