# Contract Chat image attachments and quote-reply

- Status: In progress (Stage 1 of 5 complete)
- Active owner/tool: Claude
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `4812985`
- Last known commit: (uncommitted - Stage 1 work in progress)

## Goal

Build the final item of the original 6-item Contract Chat redesign plan:
image upload (multimodal input across Gemini/GPT-4o/Claude) and
quote-and-reply. See ADR-008 for the full design and reasoning. Staged
implementation, each stage independently reported and verified:

1. Storage + Attachment entity + upload/retrieval endpoints + tenant
   isolation tests. **(this stage)**
2. Provider image-format wiring + explicit vision-capability gate +
   cross-provider conversion regression test.
3. Frontend attach/preview/render UI (live + restored).
4. Quote-reply backend (context threading, persistence, citation
   snapshot, confirmed never re-fed into Output Guard).
5. Quote-reply UI (selection capture, quote banner, dismiss).

## Non-goals

- Production deployment/push.
- Any change to the two protected grouped-extraction research files.
- Retention/purge tooling for orphaned (uploaded-but-never-sent)
  attachments - same accepted gap as the PDF pipeline's own orphan case.
- GIF or any animated image format (see ADR-008).
- Session archive/delete cascading - chat sessions have no delete/archive
  endpoint at all today; nothing to cascade yet.

## Acceptance criteria

- [x] Stage 1: encrypted, tenant+session-scoped attachment storage
  (AES-256-GCM, mirroring `pdf_source_storage.py`).
- [x] Stage 1: `ChatAttachment` Neo4j entity + migration +
  `HAS_ATTACHMENT` relationship.
- [x] Stage 1: authenticated upload (`POST .../attachments`, multipart,
  5MB limit, PNG/JPEG/WEBP magic-byte validation) and retrieval
  (`GET .../attachments/{id}`) endpoints.
- [x] Stage 1: upload endpoint has its own rate limit
  (`CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT`, tenant-scoped).
- [x] Stage 1: cross-tenant AND cross-session isolation tests at both
  repository and route level.
- [ ] Stage 2: single provider-agnostic image content-block format feeds
  Gemini/GPT-4o/Claude with zero per-provider branching; explicit
  rejection for non-vision models.
- [ ] Stage 3: attach/preview/render UI, including correct rendering on
  session restoration.
- [ ] Stage 4: quote-reply reaches the model as real context; persists and
  restores correctly; citation snapshot is UI-only, never re-grounds.
- [ ] Stage 5: quote selection/banner/dismiss UI.

## Invariants affected

- Tenant/session isolation on every new endpoint (upload, retrieval)
- Encrypted-at-rest storage, authenticated-only retrieval, no public URLs
- Explicit failure (no silent fallback) for unsupported provider/vision
  combinations
- Rate limiting and timeouts on new provider-facing calls
- Output Guard's grounding boundary (quote-reply citations must never
  cross it - Stage 4)

## Expected components/files

- `backend/infrastructure/chat_attachment_storage.py` (new)
- `backend/infrastructure/chat_session_repository.py` (attachment CRUD)
- `backend/migrations/chat_attachment_schema_migration.py` (new)
- `backend/api/chat_sessions.py` (upload/retrieval routes)
- `backend/shared/middleware/rate_limit.py` (new rate limit constant)
- `docker-compose.yml` / `docker-compose.prod.yml` (new volume)
- `docs/adr/008-contract-chat-attachments-and-quote-reply.md` (new)
- Later stages: `backend/contract_chat_agent.py`, `backend/main.py`,
  `backend/model_registry.py` (Stage 2); frontend `input.tsx`,
  `message.tsx`, `provider.tsx`, `sessionMessages.ts`, a new attachment
  API service file (Stage 3, 5); `chat_session_repository.py`'s message
  schema (`quoted_text`/`quoted_message_id`/`quoted_citations`, Stage 4).

## Verification commands

- `source backend/.venv/bin/activate && python -m pytest backend/tests/ -q`
- `docker compose -f docker-compose.yml config --quiet`
- `docker compose -f docker-compose.prod.yml config --quiet` (with
  placeholder env vars, matching this branch's established pattern)
- Live verification against the local dev stack per stage.

## Decisions

See ADR-008 for the full design record (image format, storage/lifecycle,
quote-excerpt-vs-full-message, citation non-regrounding). Stage-specific
implementation notes:

- Attachment `storage_key` is a pure function of
  `(tenant_id, session_id, attachment_id)`, computed before any graph
  write - encrypt-and-store happens first, keyed off a freshly generated
  id, then the graph row is created once with the real storage_key/mime_
  type already known. Avoids a placeholder-then-update two-step dance and
  avoids a moment where a graph node claims a storage_key that doesn't
  exist yet.
- `get_attachment` requires an exact `(attachment_id, tenant_id,
  session_id)` match - a tenant's own attachment from a *different* one of
  their own sessions is still rejected, not just cross-tenant reads.

## Work completed

**Stage 1** (this pass):
- `ChatAttachmentStorage` (AES-256-GCM, AAD-bound to tenant+session+
  attachment identity, magic-byte format sniffing, path-traversal
  defense) - `backend/infrastructure/chat_attachment_storage.py`.
- Four repository methods on `Neo4jChatSessionRepository`:
  `create_attachment`, `get_attachment`, `link_attachment_to_message`,
  `list_attachments_for_message` - all tenant/session-scoped via MATCH
  predicates, same posture as every other write path in that repository.
- `ChatAttachmentSchemaMigration` (constraint + tenant/session indexes),
  registered in `run_all_migrations.MIGRATIONS`.
- Two new routes in `backend/api/chat_sessions.py`:
  `POST /api/chat/sessions/{session_id}/attachments` (multipart, rate-
  limited, 5MB limit, magic-byte format validation) and
  `GET /api/chat/sessions/{session_id}/attachments/{attachment_id}`
  (authenticated retrieval, mirrors `get_contract_source_pdf`).
- `CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT` (20/min/tenant) in
  `rate_limit.py`, same `tenant_scoped_or_ip_key` mechanism as
  `CHAT_RUN_RATE_LIMIT`.
- `chat_attachment_data` volume added to both `docker-compose.yml` and
  `docker-compose.prod.yml` (backend-only, mirroring `pdf_source_data`'s
  posture - neither nginx nor the Celery worker can reach it).
- ADR-008 written; `docs/adr/README.md` index updated (moved "Attachment
  storage and lifecycle" from decision candidates to accepted).
- 5 new test files, 49 new tests total: `test_chat_attachment_storage.py`
  (16), `test_chat_attachment_repository.py` (13),
  `test_chat_attachment_schema_migration.py` (5),
  `test_chat_attachment_tenant_isolation.py` (7),
  `test_chat_attachment_api.py` (8).
- `test_migration_consolidation.py`'s exact-count assertion updated
  (14 -> 15 registered migrations) alongside registering
  `chat_attachment_schema` - kept in the same commit as the registration
  itself so the suite is green at this commit, not just at a later one.

## Work remaining

Stages 2-5, per the acceptance criteria above.

## Failing checks

None - full suite green (913 passed/1 skipped after this stage).

## Recommended next action

Proceed to Stage 2 (provider image-format wiring, vision-capability gate,
cross-provider conversion test) after this pass's report is reviewed.
