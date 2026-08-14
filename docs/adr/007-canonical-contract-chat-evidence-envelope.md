# ADR-007: Canonical Contract Chat evidence envelope

- Status: Accepted
- Date: 2026-08-08
- Owners: architecture, security/tenancy, legal-data governance, and application roles
- Related task/PR: `docs/tasks/active/output-guard-evidence-repair.md`

## Context

Contract Chat generation consumed heterogeneous tool messages, while Output Guard
later reconstructed a plain source string and citations were derived through a
separate parse after validation. Contract IDs, source classes, per-contract
grouping, locators, deterministic counts, and stable source identity could be
lost at those boundaries. A model could also answer a factual first turn without
calling a tool, leaving the fail-closed guard with no current-turn evidence.

This caused valid catalog and broad comparison prompts to be withheld even though
the system could retrieve authoritative tenant data. Page provenance is not
applicable to every true claim: a tenant-scoped active-contract count is metadata,
while a payment term requires contract text.

## Decision

Every Contract Chat tool result is normalized immediately after the server injects
authenticated tenant, correlation, and optional contract scope. The answer model,
citation builder, and Output Guard consume the same provider-neutral
`chat-evidence-v1` envelope. Historical tool messages cannot ground a new turn.

Each evidence record has a stable content-derived `EVID_…` identity bound to the
tenant, source type, contract, locator, excerpt, and facts. Applicable fields
preserve active tenant-authorized contract ID and server-resolved filename,
bounded facts/excerpt, page/section/clause/chunk locator, tool-call identity,
retrieval score, and verification status. Supported source classes are
`document_metadata`, `document_text`, `section`, `clause`, `chunk`,
`relationship`, `deterministic_aggregation`, `policy_rule`, and
`analysis_result`. Tool failures retain only bounded status/category, never raw
provider errors.

The claim/evidence policy is:

1. Grounded factual claims may pass when supported by authorized evidence.
2. Tenant-scoped metadata/list/count facts may pass without a PDF page.
3. Qualified synthesis may pass when its material premises are supported.
4. Textual legal/commercial terms require text-bearing evidence; metadata alone
   is insufficient.
5. Cross-contract claims require supporting evidence for each named contract.
6. Unsupported or contradicted material claims, unknown evidence IDs, archived
   evidence, cross-tenant evidence, and empty retrieval fail closed.
7. Tool content is untrusted data, never instructions. Model-authored raw Cypher
   is not forwarded by Contract Chat.
8. Validator infrastructure failure, timeout, Prompt Guard rejection, missing
   evidence, and unsupported content remain distinct terminal outcomes.

Citation IDs use the same canonical evidence IDs and are revalidated against
current active tenant ownership before release and restoration. Deterministic
checks cover envelope identity, citation membership, tenant/active ownership,
counts, filenames, and text-versus-metadata sufficiency. The semantic validator
uses a strict JSON decision schema and cannot convert malformed output or an
exception into a pass. Simple catalog/count/type/party questions may be rendered
deterministically from authorized metadata; this is not a second model rewrite.

## Alternatives considered

- Concatenate tool output into source prose: rejected because it loses typed
  provenance and deterministic facts.
- Treat any citation as sufficient grounding: rejected because a citation may
  not support the material claim.
- Require a PDF page for every claim: rejected because authoritative metadata
  and deterministic aggregation do not inherently have page provenance.
- Add an answer-rewrite model call: rejected because it adds cost, latency, and
  another unsupported-claim boundary.
- Restore fail-open exceptions or lower guard standards: rejected because a
  validator failure is not evidence of safety or grounding.

## Consequences

Tool results are bounded and normalized once, with extra active-tenant graph
checks before contract-owned evidence enters the envelope. This adds graph work
and validation latency but makes generation, grounding, citations, audit shape,
and restoration consistent. Catalog/count routing is deterministic; comparisons
take precedence over overlapping catalog words. Prompt/model version changes are
explicit (`contract-chat-v2-evidence`).

The envelope is persisted inside encrypted tool messages. Citation records retain
the minimum encrypted facts/locator needed to recompute evidence identity on
restore. The additive `terminal_reason` property is nullable, so no destructive
migration or legacy backfill is required. Local success does not establish
e2-micro capacity or live-provider equivalence.

## Verification and observability

Tests cover real nested all-level search shapes, metadata/count answers, cross-
contract evidence, unknown IDs, wrong counts, invented filenames, archived and
cross-tenant evidence, prompt-like evidence, strict validator failures, MCP status,
persistence/restoration, SSE outcomes, PDF citations, model routing, and
cancellation. Runtime logs expose only bounded evidence counts/source types,
contract counts, located-evidence counts, terminal categories, and correlation
IDs—never prompts, answers, excerpts, raw tool results, or validator payloads.

## Rollout and rollback

No production rollout is authorized by this ADR. Rollback must keep Output Guard
fail closed and preserve encrypted message/citation compatibility. If the envelope
path cannot be validated, disable Contract Chat or revert the whole coherent
change; do not fall back to raw string evidence, model-authored Cypher, or
unvalidated answer release.
