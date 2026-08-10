# ADR-008: Contract Chat image attachments and quote-reply

- Status: Accepted
- Date: 2026-08-10
- Owners: architecture, security/tenancy, application roles
- Related task/PR: `docs/tasks/active/chat-attachments-and-quote-reply.md`

## Context

The last item of the original Contract Chat redesign plan (image upload +
quote-reply) had zero scaffolding: no Attachment entity, no
`HAS_ATTACHMENT` relationship (despite it being named as a forward-compat
hook in the original persistent-sessions plan), no backend support, no UI.
This is genuinely new architecture, covering a new provider capability
(vision), new tenant-scoped encrypted storage, and two new UI interaction
patterns with no existing infrastructure to extend.

Design research (full package-source reads, not documentation guesses)
confirmed two load-bearing facts before any code was written:

1. `langchain_core` (1.5.2, as installed) documents exactly one
   provider-agnostic v1 content-block format for images
   (`ImageContentBlock`: `{"type": "image", "base64"|"url", "mime_type"}`),
   and each of `langchain-google-genai` (4.3.2), `langchain-openai` (1.4.1),
   and `langchain-anthropic` (1.5.3) independently detects and converts it
   to their own real wire format internally. No provider branching is
   needed in this codebase's own message-building code.
2. `model_registry.py`'s `ModelSpec.capabilities` already tags `"vision"`
   on `gemini-2.5-flash`, `gpt-4o`, and `claude-sonnet-5` (not
   `mistral-large`) - anticipated but unused until this feature.

## Decision

### Image format (implemented in Stage 2)

Build `HumanMessage(content=[{"type": "text", "text": prompt}, {"type":
"image", "base64": b64, "mime_type": mime}])` at the same call site that
builds today's plain-string `HumanMessage`. No `if spec.provider == ...`
branching. A turn carrying an attachment against a model whose spec lacks
`"vision"` in `capabilities` is rejected explicitly (400,
`category: "vision_unsupported"`) before any provider call - reusing the
registry's existing capability-filtering shape, not inventing new
plumbing. No silent substitution or degrade.

### Storage and Attachment entity (implemented in Stage 1)

Mirrors `pdf_source_storage.py`'s AES-GCM pattern exactly - not a new
security model. `ChatAttachmentStorage`
(`backend/infrastructure/chat_attachment_storage.py`) encrypts each image
with AES-256-GCM, AAD-bound to `(tenant_id, session_id, attachment_id)` -
tighter than PDF storage's `(tenant_id, contract_id)` binding, since
attachments are session-scoped conversation artifacts, not tenant-wide
canonical documents. The storage key is a SHA-256 hex digest of that same
triple, never accepted from an HTTP caller, with the same path-traversal
defense-in-depth as `pdf_source_storage.py`'s `_path()`.

Real image format is sniffed from magic bytes
(`detect_image_mime_type`), never trusted from the client's declared
`Content-Type` - same "trust the bytes" posture as
`pdf_source_storage.py`'s `content.startswith(b"%PDF-")` check. Allowlist:
PNG, JPEG, WEBP. GIF is deliberately excluded: Anthropic doesn't handle
animated frames the same way Gemini/OpenAI do, and a narrower, uniformly-
safe allowlist beats a wider one with per-provider surprises. Limit: 5MB
per image - the tightest of the three configured providers' real per-image
ceilings (Anthropic's); going higher would let Claude turns hit an
explicit provider rejection that Gemini/GPT-4o wouldn't, an inconsistency
avoided rather than accepted. Up to 4 images per message.

New Neo4j node: `(:ChatAttachment {attachment_id, tenant_id, session_id,
mime_type, size_bytes, storage_key, created_at})`, related via
`(:ChatMessage)-[:HAS_ATTACHMENT]->(:ChatAttachment)` (mirroring
`(:Document)-[:HAS_CHUNK]->(:Chunk)`'s convention, using the exact
relationship name the original persistent-sessions plan already
anticipated). `tenant_id` and `session_id` are both node properties, not
solely derivable from a relationship - same isolation-via-property-filter
posture as `ChatSession`/`ChatMessage`.

Two-phase upload-then-send flow (an attachment must exist before the
message referencing it does): `POST /api/chat/sessions/{session_id}/
attachments` (multipart, matching this codebase's one existing upload
precedent, `enhancedSearchApi.ts`) encrypts-and-stores first, keyed off a
freshly generated `attachment_id`, then writes the graph row once - never
the reverse order, which would let a graph row claim a `storage_key` that
doesn't exist yet. `GET /api/chat/sessions/{session_id}/attachments/
{attachment_id}` mirrors `GET /api/documents/{contract_id}/source`
exactly: ownership-checked (tenant AND session), 404 for missing/cross-
tenant/cross-session indistinguishably, `Cache-Control: private,
no-store`, `X-Content-Type-Options: nosniff`, no public URL ever exists.

Rate limiting: the existing `CHAT_RUN_RATE_LIMIT` (30/min/tenant) already
bounds image-bearing generation calls - call-frequency limiting doesn't
need to scale with a single call's cost. The new upload endpoint is a
separate, disk-write operation reachable independent of `/api/run/`, so it
gets its own bound: `CHAT_ATTACHMENT_UPLOAD_RATE_LIMIT` (20/min/tenant,
same `tenant_scoped_or_ip_key`).

Lifecycle: chat sessions have no delete/archive endpoint today
(`archived_at` is defensively filtered everywhere but nothing ever sets
it - pure unused scaffolding). Attachments therefore follow the same
soft-archive convention already established for contracts (ADR-003) for
when session archival eventually gets built - never physically deleted
alongside a session archive. An uploaded-but-never-sent attachment is a
known, accepted orphan class, the same as the PDF pipeline's own orphan
case (`docs/tasks/active/pdf-citations-model-selection.md`'s risks) - no
new retention/purge tooling is built here.

### Quote-and-reply (implemented in Stage 4/5)

Quotes the user's actual **selection** (an excerpt), not the full prior
message - matching the real Claude/ChatGPT UX pattern this feature is
modeled on, keeping prompt tokens bounded and precise, and avoiding the
"broad span dilutes precision" problem the citation-highlight fix earlier
in this engagement already solved for retrieved evidence. Falls back to
the full message only when nothing was selected.

Structured persistence, not baked into message content: new nullable
`ChatMessage` properties `quoted_text` (the excerpt) and
`quoted_message_id` (informational reference, not a security boundary -
the source message is already tenant/session-scoped). `runner()` composes
a delimited quote+prompt block into the `HumanMessage` sent to the
provider fresh from these fields at model-call time - never stored pre-
formatted - so restoration (`_messages_from_stored`) can recompose the
identical framing for later turns in a restored session.

Citation re-surfacing is informational only, never re-grounding: if the
quoted excerpt's source is an AI message with citations, that message's
entire `citations` array is snapshotted onto the new `user_message` row as
`quoted_citations` for UI display (a "quoting: Clean_MSA.pdf p.1" chip) -
not fuzzy-matched down to a single citation (avoiding the same fragile
substring-matching problem already avoided elsewhere). This is **never**
fed back into Output Guard's grounding check for the new turn - the new
turn still goes through real retrieval and Output Guard independently.
Reusing old evidence to "prove" a new answer's grounding would be exactly
the silent-evidence-reuse problem ADR-004 already guards against, and
matches the original persistent-sessions plan's own restraint (replay only
text into history, never raw evidence, for the same reason).

## Alternatives considered

- **A single provider-specific image format, branched per provider in this
  codebase's own code**: rejected - the installed langchain packages
  already do this translation correctly and are the tested, maintained
  place for it; duplicating it here would be a second, driftable
  implementation with no benefit.
- **A public/pre-signed URL for image retrieval** (simpler client code):
  rejected for the same reason PDF sources never got one - tenant images
  are encrypted, private data; no public URL should ever exist for them,
  and providers can't fetch an authenticated URL themselves anyway (base64
  inline is the only real option for feeding images to a model).
- **Storing the full prior message on quote-reply** (simpler, no selection
  capture needed): rejected - dilutes prompt precision for exactly the
  same reason a full 1200-char excerpt made a poor citation highlight,
  and doesn't match the real UX pattern this feature is modeled on.
- **Fuzzy-matching a quoted excerpt back to one specific citation**:
  rejected as fragile (substring matching against `highlight_text` across
  HTML-rendered/whitespace-normalized text) in favor of snapshotting the
  whole source message's citation set, which is simple, bounded, and
  already-encrypted-at-rest via the existing citation persistence path.
- **A single combined upload+send request** (image bytes inline in
  `/api/run/`'s body): rejected - that endpoint is SSE/JSON-only today;
  mixing multipart binary upload into it would complicate the existing
  rate-limit/stall-timeout work without a real benefit, versus a separate
  upload-then-reference-by-id flow that mirrors the existing PDF contract
  upload precedent.

## Consequences

New storage subsystem (encrypted volume, `chat_attachment_data`, backend-
only - neither nginx nor the Celery worker can reach it, mirroring
`pdf_source_data`'s posture) and a new Neo4j entity/migration. Real, billed
vision-capable API calls add cost beyond today's text-only generation -
bounded by the same `CHAT_RUN_RATE_LIMIT` plus the new upload-specific
limit. An uploaded-but-abandoned attachment is a known, accepted storage
orphan (see Lifecycle above) - not a new gap, the same class already
flagged for the PDF pipeline. Quote-reply's `quoted_citations` are
UI-only, never fed to Output Guard - a restored session's later turns
still can't claim old evidence grounds a new answer.

## Verification and observability

Stage 1: attachment storage encryption-at-rest round trip, identity-
binding (wrong tenant/session/attachment_id all fail closed), path-safety,
repository CRUD, route-level and repository-level cross-tenant/cross-
session isolation, upload endpoint size/format validation and its own
rate limit - all with real regression tests, not just manual verification.
Later stages (provider wiring, frontend, quote-reply) get the same
discipline, each reported and verified independently before the next
begins, including a cross-provider regression test proving the same
content-block dict produces each provider's own correct real wire shape
(the test that would catch a future langchain upgrade silently breaking
this design's central "one format, zero branching" guarantee).

## Rollout and rollback

Additive migration (`chat_attachment_schema_migration.py`), no backfill
required - existing sessions/messages are unaffected until a client
actually uploads an attachment or sends a quote-reply. Rolling back is
deleting the new routes/migration/volume mount; no destructive step is
required since nothing existing depends on the new entity. Nothing in this
ADR authorizes production deployment.
