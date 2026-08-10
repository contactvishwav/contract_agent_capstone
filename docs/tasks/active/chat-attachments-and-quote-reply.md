# Contract Chat image attachments and quote-reply

- Status: In progress (Stage 2 of 5 complete)
- Active owner/tool: Claude
- Branch: `feat/persistent-chat-sessions`
- Worktree: `/Users/vishwa/contract_agent_capstone_copy`
- Base commit: `4812985`
- Last known commit: `f36b504` (Stage 1)

## Goal

Build the final item of the original 6-item Contract Chat redesign plan:
image upload (multimodal input across Gemini/GPT-4o/Claude) and
quote-and-reply. See ADR-008 for the full design and reasoning. Staged
implementation, each stage independently reported and verified:

1. Storage + Attachment entity + upload/retrieval endpoints + tenant
   isolation tests.
2. Provider image-format wiring + explicit vision-capability gate +
   cross-provider conversion regression test. **(this stage)**
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
- [x] Stage 2: single provider-agnostic image content-block format feeds
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
- `backend/contract_chat_agent.py`, `backend/main.py`,
  `backend/llm_manager.py` (Stage 2)
- Later stages: frontend `input.tsx`, `message.tsx`, `provider.tsx`,
  `sessionMessages.ts`, a new attachment API service file (Stage 3, 5);
  `chat_session_repository.py`'s message schema
  (`quoted_text`/`quoted_message_id`/`quoted_citations`, Stage 4).

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
  type already known.
- `get_attachment` requires an exact `(attachment_id, tenant_id,
  session_id)` match - a tenant's own attachment from a *different* one of
  their own sessions is still rejected, not just cross-tenant reads.

## Work completed

**Stage 1**:
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

**Stage 2** (this pass):
- `_build_prompt_message()` (`backend/main.py`) - builds the shared,
  provider-agnostic content-block list (`{"type": "image", "base64",
  "mime_type"}`) alongside the text block; unchanged plain-string
  behavior when no attachment is present.
- `runner()` gained `attachment_ids`; links each uploaded attachment to
  the persisted `user_message` row via `link_attachment_to_message` once
  it exists.
- `/api/run/` route: explicit vision-capability gate (`selected_spec.
  capabilities` must include `"vision"`), attachment ownership
  pre-validation, and a session-required check - all before
  `StreamingResponse` starts, same "clean 400" reasoning as the existing
  contract/session checks.
- `RunPayload.attachment_ids: Optional[List[str]]`.
- 22 new tests: `test_chat_image_cross_provider_conversion.py` (4, the
  literal same-dict-through-each-real-conversion-function proof),
  `test_chat_image_attachment_wiring.py` (9: `_build_prompt_message`
  unit tests, vision-gate route tests including "no attachment leaves a
  non-vision model unaffected" and "cross-tenant attachment 404s", and
  a real end-to-end `runner()` test proving the model receives the
  correct multimodal content and the attachment gets linked),
  `test_chat_image_message_text_extraction.py` (7, the regression test
  for the bug found live - see below).

**Interim fix pass** (between Stage 2 and Stage 3, both explicitly
requested and both now resolved):

- **Output Guard grounding gap for image-only turns, fixed narrowly**
  (ADR-004 addendum). New `image_attachment_evidence_item()`
  (`chat_evidence_service.py`) represents an attached image the
  responding model directly examined as a real evidence item;
  `runner()` appends one per loaded attachment to the combined evidence
  envelope. `image_attachment` is a new `SOURCE_TYPES` entry but is
  deliberately excluded from the `text_source_present` set
  `hallucination.py`'s deterministic legal-terms check relies on, so a
  turn mixing an image with real contract claims still requires real
  chunk/section/clause/document_text evidence for those claims,
  unchanged. `HallucinationValidator`'s LLM-auditor prompt gained one
  new guideline explaining image_attachment evidence grounds only
  image-describing claims. `contract_id` is always `None` on this
  evidence type, so citation building automatically skips it (same
  existing behavior as `deterministic_aggregation`) - no bogus citation
  is ever created for an attachment. 4 new tests in
  `test_chat_evidence_lifecycle.py` (evidence-item shape, pure-image
  claim grounded, image-alone-does-not-ground-a-legal-claim, mixed
  image+chunk answer still grounded). Live-verified: the same GPT-4o
  pure-image question that previously rejected as `insufficient_scope`
  now returns `"status": "passed"` with the real, correct vision answer
  ("The image contains a blue circle on the left and a red rectangle on
  the right").
- **Claude `temperature` bug, fixed**. Researched: Anthropic deprecated
  `temperature`/`top_p`/`top_k` entirely for Claude Opus 4.7+ and Claude
  Sonnet 5 (shipped 2026-06-30) - the parameter's mere *presence* 400s,
  confirmed via Anthropic's own migration guide and "What's new in
  Claude Sonnet 5" docs, and empirically verified live (omitting the
  parameter entirely succeeds; no other value was tried as a
  substitute, matching Anthropic's own guidance). Fixed by omitting
  `temperature` from `ChatAnthropic(...)` construction in
  `llm_manager.py` - Anthropic-only; every other provider is unchanged.
  2 new tests in `test_llm_manager_provider_construction.py`. Live-
  verified: a real `claude-sonnet-5` chat turn now returns
  `"status": "passed"` with a real generated answer (visible in the raw
  history event), no more 400.

## Work remaining

Stages 3-5, per the acceptance criteria above.

## Failing checks

None - full suite green (913 passed/1 skipped after Stage 1, 935 after
Stage 2, 941 after this interim Output-Guard/Claude fix pass).

## Recommended next action

Proceed to Stage 3 (frontend attach/preview/render UI) after this pass's
report is reviewed.
