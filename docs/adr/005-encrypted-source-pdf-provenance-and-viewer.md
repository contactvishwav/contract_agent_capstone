# ADR-005: Encrypted source-PDF provenance and authenticated viewer

- Status: Accepted
- Date: 2026-08-08
- Owners: architecture, security/tenancy, graph/data, and frontend roles
- Related task/PR: `docs/tasks/active/pdf-citations-model-selection.md`

## Context

Uploads previously extracted text from a temporary PDF and deleted the original.
The extractor concatenated pages, so citations could retain an excerpt or whole-
document offsets but could not truthfully identify a PDF page. Private legal PDFs
cannot be placed at public URLs, and legacy contracts cannot be backfilled when
their original bytes no longer exist.

## Decision

For new standard and enhanced uploads, retain the exact uploaded bytes in an
application-owned named volume using AES-GCM envelope storage and an opaque,
content-derived storage key. Record only the opaque key and bounded metadata on
the tenant-owned active `Contract`. Store page-local extracted text as encrypted
tenant/contract-bound `SourcePage` nodes with PDF page numbering, global spans,
text-layer availability, and extraction version.

Serve bytes only from `GET /api/documents/{contract_id}/source`. The endpoint
derives tenant from authenticated RBAC context, rechecks active lifecycle and
ownership, resolves storage server-side, and returns a common 404 for missing,
archived, cross-tenant, or unavailable sources. It uses PDF content type, encoded
attachment filename, and private no-store caching; tokens and storage paths never
enter the URL.

Citations discard tool/stored locator claims and re-resolve against current
authorized provenance. The UI uses an in-memory authenticated PDF.js modal so
closing returns to the same chat. Locator tiers are: unique normalized exact text
match (page plus highlight), verified chunk page (page only), section/clause or
excerpt preview, and unavailable/image-only. No fuzzy first-match is allowed.

## Alternatives considered

- Public/static PDF URLs: rejected because possession would bypass tenant and
  lifecycle authorization.
- Browser object URLs: unnecessary; PDF.js accepts authenticated response bytes
  directly, avoiding URL lifetime and revocation risk.
- Infer pages for legacy rows: rejected because the page boundary was discarded.
- Dedicated viewer route: deferred because navigation is currently in-memory and
  a modal preserves session state without an unrelated routing redesign.
- OCR coordinates: deferred; no OCR engine or coordinate provenance exists.

## Consequences

New uploads consume durable encrypted disk in addition to Neo4j. The named volume
is now evidence-bearing data and must be backed up and capacity-monitored. Text-
layer rendering and the PDF.js worker increase frontend bundle size and browser
memory; locally packaged standard-font/CMap assets add about 2.4 MB of uncompressed
static files. This matters on slow clients, while encrypted byte loading affects backend
disk/CPU on the e2-micro. Legacy and scanned PDFs remain truthful but cannot offer
exact deep links. Archive denies delivery but does not destroy evidence; deletion
needs its own accepted retention policy.

## Verification and observability

Tests cover tenant-bound encrypted storage, traversal rejection, active/tenant
authorization, safe headers, page identity, exact/ambiguous/whitespace matching,
page-only/excerpt/image-only tiers, citation revalidation, PDF.js loading, highlight
selection, and keyboard close. Browser acceptance must independently confirm the
displayed PDF page contains the highlighted text.

## Rollout and rollback

The additive migration creates `SourcePage` constraints/indexes; it performs no
legacy backfill. Rollback may hide citation links and stop writing new provenance,
but must preserve the named volume and existing metadata until retention policy is
decided. Production rollout requires volume backup/capacity and e2-micro resource
verification. This ADR does not authorize deployment.
