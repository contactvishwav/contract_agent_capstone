# ADR-002: Standalone MCP is local-only until external authentication exists

- Status: Accepted
- Date: 2026-08-08
- Owners: security/tenancy and MCP roles
- Related task/PR: `docs/tasks/active/tenant-authorization-traceability.md`

## Context

The in-process FastAPI-to-MCP bridge begins with a validated JWT tenant and
can bind that tenant server-side. The standalone FastMCP stdio process has no
principal authentication or server-side principal-to-tenant mapping. Requiring
a `tenant_id` tool argument validates presence, not authority, so the previous
README claim of secure external multi-tenancy was unsupported.

## Decision

In-process calls require an explicit authenticated tenant supplied outside the
generic tool argument bag. The bridge removes caller tenant arguments, injects
the authenticated value, and binds it as the MCP principal for tool execution.

Standalone MCP is trusted-local development functionality only. Startup needs
both `ENVIRONMENT=development|test` and
`MCP_STANDALONE_LOCAL_ONLY=true`; production always fails closed. In local
mode the OS/process operator is the trust boundary and may assert a tenant.
This is not external authentication and must not be described as such.

## Alternatives considered

- Continue accepting mandatory tenant arguments: rejected because possession
  or invention of an identifier is not authentication.
- Implement ad-hoc API keys in tool arguments: rejected as a partial security
  layer without lifecycle, principal mapping, rotation, or transport design.
- Remove standalone MCP entirely: unnecessary while its local trust boundary
  is explicit and production is closed.

## Consequences

Existing local standalone users must opt in explicitly. Network/external MCP
support is deferred until an authenticated principal design is accepted.
Contract Chat's in-process MCP tools retain their current capabilities while
gaining defense-in-depth against tenant overrides.

## Verification and observability

Tests cover missing bridge identity, override attempts, all four data paths,
production direct calls, local opt-in, and correlation propagation. Audit
metadata records identity source and trace ID without raw tool arguments.

## Rollout and rollback

No data migration is required. Re-enabling production standalone MCP requires
a new ADR and a real external authentication implementation; toggling the
local flag is not an acceptable production rollback.
