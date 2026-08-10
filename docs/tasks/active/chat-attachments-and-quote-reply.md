# Contract Chat image attachments and quote-reply

- Status: In progress (Stage 3 of 5 complete)
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
- [x] Stage 2: single provider-agnostic image content-block format feeds
  Gemini/GPT-4o/Claude with zero per-provider branching; explicit
  rejection for non-vision models.
- [x] Stage 3: attach/preview/render UI, including correct rendering on
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
  `test_chat_image_attachment_wiring.py` (11: `_build_prompt_message`
  unit tests, vision-gate route tests including "no attachment leaves a
  non-vision model unaffected" and "cross-tenant attachment 404s", and
  a real end-to-end `runner()` test proving the model receives the
  correct multimodal content and the attachment gets linked),
  `test_chat_image_message_text_extraction.py` (7, the regression test
  for the bug found live - see below).
- One pre-existing test updated legitimately:
  `test_all_real_migrations_registered_in_correct_order`'s exact-count
  assertion (this was actually a Stage 1 leftover, caught and fixed in
  this pass's full-suite run).

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

**Stage 3** (this pass):
- Backend addition needed to make restoration possible at all:
  `MessageResponse.attachments` (`backend/api/chat_sessions.py`) - only
  `attachment_id`/`mime_type` reach the client (not `size_bytes`/
  `created_at`, a Neo4j datetime not directly JSON-serializable and not
  needed for rendering); scoped to `user_message` rows only (attachments
  are only ever linked there).
- Also closed a real gap found while implementing this stage: the
  "4 images per message" limit was documented in ADR-008 but never
  actually enforced in code - added `MAX_ATTACHMENTS_PER_MESSAGE` check
  to `/api/run/`, before any provider call, same pattern as the vision
  gate.
- `frontend/src/services/chatAttachmentApi.ts` (new): upload (multipart),
  authenticated image fetch as an object URL, client-side validation
  mirroring the server's real limits (5MB/PNG-JPEG-WEBP/4-per-message),
  a distinct `AttachmentUploadError` carrying the server's real rejection
  reason (size/format/rate-limit/network) - no silent failures.
- `MessagePartType` gained `"attachments"` (`provider.tsx`) - same
  JSON-stringified synthetic-part bolt-on pattern `"citations"` already
  established, so live-sent and restored messages render identically
  with zero special-casing.
- `AttachmentImage.tsx` (new): authenticated fetch (never a public URL,
  same posture as `PdfCitationViewer.tsx`), loading/ready/unavailable
  states, click-to-enlarge lightbox.
- `input.tsx`: attach button (hidden file input + trigger), thumbnail
  preview row with per-attachment uploading/error states and remove,
  two-phase upload-on-select (not deferred to send) so most uploads have
  already finished by the time the user finishes typing, session created
  lazily on first attach if none exists yet (`ensureSession`, shared with
  the existing first-send lazy creation), Send disabled while any
  attachment is uploading/errored/unsupported-by-the-selected-model, a
  real inline vision-gate warning (using `ModelOption.capabilities`,
  already returned by the existing models endpoint - zero extra
  requests) rather than waiting for the server's 400.
- `sessionMessages.ts`: restores the `"attachments"` part from
  `ChatSessionMessage.attachments`, ordered before the text part.
- 55 new tests total: 6 new backend (`test_chat_session_detail_
  attachments.py` ×4, attachment-count-limit ×2), 11 new frontend
  (`ChatAttachments.test.tsx` - client-side validation, loading/error
  states, remove, 4-image limit, vision-gate warning, lazy session
  creation on attach, live-send request body + rendering, restored-
  history rendering). One pre-existing frontend test file
  (`ChatMessage.test.tsx`) needed a `useChatSession` mock added, since
  `ChatMessage` now resolves `session_id` for attachment fetches -  a
  real, necessary consequence of this stage's change, not a workaround.

## Work remaining

Stages 4-5, per the acceptance criteria above.

## Failing checks

None - full suite green throughout (913 passed/1 skipped after Stage 1,
935 after Stage 2, 941 after the interim Output-Guard/Claude fix pass,
947 after Stage 3). Frontend: 40/40 tests, clean production build, lint
at the exact same pre-existing baseline (17 errors/7 warnings) - zero
new lint issues introduced.

## Checks not run

- **Real browser verification was not possible this pass**: no browser-
  automation tool (Playwright/Puppeteer/similar) is available in this
  session. Not fabricated or approximated as "done." Substituted with
  the closest honest proxy: drove the exact same API sequence the real
  frontend calls (upload → send a real question against gpt-4o with a
  real test image → fetch session detail as a page refresh would →
  re-fetch the image bytes as `AttachmentImage` would after that
  refresh) - confirmed a genuine, correct vision answer ("The image
  contains a green circle and a purple triangle," matching the actual
  test image), confirmed the restored session detail carries the
  `attachments` field correctly, and confirmed the re-fetched image
  bytes are byte-identical to the original upload. This proves the
  backend contract the UI depends on is correct end-to-end with real
  data and a real provider - it does not prove the actual bundled
  frontend renders correctly in a real browser. A manual pass in a real
  browser is recommended before considering Stage 3 fully closed.

## Changed/uncommitted files

All Stage 1-3 and interim-fix files remain uncommitted pending user
review, matching this branch's established per-stage reporting
discipline.

## Risks/questions

- Real browser verification still outstanding - see "Checks not run"
  above. Recommend a manual pass (attach, send, navigate away/back,
  refresh) before this stage is considered fully closed.
- Minor, disclosed UX limitation: if a session is created by attaching
  an image before typing any text, its title defaults to "New chat"
  (the server's default when no title is given) rather than being
  derived from the first message, since no message text exists yet at
  attach time. Not fixed - a rename-after-the-fact would add real
  complexity for a small cosmetic gain; flagging rather than silently
  accepting without mention.
- None currently open from Stage 2's interim fix pass (Output Guard
  grounding gap, Claude's temperature incompatibility) - both fixed,
  tested, and live-verified.
- None new beyond what ADR-008 already names as accepted (orphaned-
  attachment retention, no session archive/delete to cascade against yet).

## Recommended next action

Proceed to Stage 4 (quote-reply backend) after this pass's
report is reviewed.
