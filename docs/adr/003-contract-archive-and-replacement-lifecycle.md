# ADR-003: Contract archive and corrected-replacement lifecycle

- Status: Accepted
- Date: 2026-08-08
- Owners: architecture, security/tenancy, and legal-data governance roles
- Related task/PR: `docs/tasks/active/product-workflow-corrections.md`

## Context

Contracts currently have no lifecycle state, filename is treated as the
duplicate key, and there is no safe user removal path. Physical deletion would
need a separately approved retention and cryptographic-erasure policy because
contracts can be referenced by Documents, chunks, extracted graph data,
analyses, chat evidence, and audit records. A broad graph delete would risk
shared nodes and destroy traceability.

## Decision

Removal in the product means tenant-scoped soft archive, not physical deletion.
Legacy contracts without a lifecycle property are `ACTIVE`. Archive sets the
Contract and its tenant-owned Document records to `ARCHIVED`, records actor and
time, and archives contract-specific ChatSession records. Those sessions and
their messages remain retained but are hidden and non-executable. All-Contracts
sessions remain available; restored citations are revalidated and omit archived
sources.

Only a role with `DELETE` permission may archive. Missing, already archived,
and cross-tenant identifiers are non-disclosing `404`s. A contract with a
`PENDING`, `STARTED`, or processing analysis is rejected with `409`; late task
results also require an active Contract before persistence. Normal contract
lists, new analysis, chat scope validation, REST search, tool-facing search,
and chunk retrieval must all predicate on active lifecycle state.

New uploads store a SHA-256 source-content hash. Duplicate detection is tenant +
active lifecycle + content hash. A different document with the same filename is
therefore a legitimate new version; an archived document can be uploaded again.
Formal supersession/version lineage is deferred. Tenant-scoped search caches are
invalidated after archive.

## Alternatives considered

- Physical cascade deletion: rejected pending retention, purge, shared-node,
  embedding, audit, and cryptographic-erasure design.
- Filename uniqueness: rejected because corrected files commonly retain names.
- Retain contract sessions as normally selectable: rejected because their
  server-authoritative scope would point to a source removed from normal use.
- Automatic task cancellation: rejected for this increment because safe Celery
  revocation semantics for already-started paid model calls are not established.

## Consequences

Archive is reversible at the data layer, retains audit evidence, and avoids
orphans because dependent evidence is retained. Archived data still consumes
Neo4j/vector storage until a separately designed purge policy exists. Legacy
uploads lack content hashes and cannot be backfilled from original bytes; hash
duplicate guarantees apply to uploads after this change.

## Verification and observability

Tests cover permission, tenant predicates, already archived and running-task
responses, active-only lists/search/chat/analysis, session hiding, content-hash
duplicates, same-name/different-content uploads, and tenant cache invalidation.
Archive emits the existing `DOCUMENT_DELETE` audit event with archive action and
contract identifier but never raw document content.

## Rollout and rollback

The lifecycle field is additive and treats missing values as active. Rollback
can stop exposing the archive action without deleting archived records. A later
unarchive or physical-purge design requires a new accepted ADR and explicit
production task.
