# ADR-001: Persisted chat-session scope is server-authoritative

- Status: Accepted
- Date: 2026-08-08
- Owners: architecture and security/tenancy roles
- Related task/PR: `docs/tasks/active/tenant-authorization-traceability.md`

## Context

`POST /api/run/` accepts both `session_id` and `contract_id`. The persisted
session already records either one specific contract or All Contracts, but
the request field previously flowed to search tools independently. A caller
could therefore execute a valid session with a different same-tenant scope.

## Decision

The persisted session scope is authoritative. `None`, an empty value, and
the existing `__all_contracts__` client sentinel normalize to All Contracts.
When a session exists, the request scope must match exactly or the server
returns `409` before streaming or writing messages. Specific contracts are
also checked with a tenant predicate at execution time; missing and
cross-tenant contracts share a `404` response.

The request field remains for compatibility and for non-session chat. It is
not silently ignored because rejection exposes stale or malicious clients
instead of concealing a scope disagreement.

## Alternatives considered

- Ignore the request field for persisted sessions: safe for retrieval, but
  hides client/server drift and makes failed scope switches appear to work.
- Remove the request field immediately: cleaner long-term, but an unnecessary
  breaking API change for ephemeral callers.
- Mutate the session scope: rejected because it silently changes conversation
  identity and can mix evidence from different contracts.

## Consequences

Existing matching clients remain compatible. Mismatched clients receive an
explicit conflict and no history mutation. Deleted contracts make their
specific sessions temporarily non-executable while preserving history.

## Verification and observability

Route tests cover matching, both mismatch directions, cross-tenant/missing
contracts, canonical All-Contracts normalization, and unchanged history.

## Rollout and rollback

No schema migration is required. Rollback is the route validation change;
persisted sessions and messages are unchanged.
