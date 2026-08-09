# ADR-006: Server model registry and explicit legal-workflow failure

- Status: Accepted
- Date: 2026-08-08
- Owners: architecture, legal-data governance, evaluation, and application roles
- Related task/PR: `docs/tasks/active/pdf-citations-model-selection.md`

## Context

Frontend model lists had drifted from provider initialization. Some selections
were accepted but ignored or normalized, analysis tools could silently instantiate
their own Gemini-first fallback chain, and UI labels could not prove which provider
served a request. Embeddings additionally require a stable dimension and cannot be
treated as another interchangeable chat choice.

## Decision

`backend/model_registry.py` is authoritative for stable IDs, private provider API
names, display labels, credential-derived availability, workflow capabilities,
production allowance, fallback eligibility, cost/latency class, and deprecation.
Authenticated `/api/models` returns only safe public fields and compatible,
configured choices. Chat, standard/enhanced upload, analysis, and Supervisor entry
points validate the stable ID before provider or task dispatch. `LLMManager` builds
exact raw provider clients and provider-specific chat graphs from that registry.

Legal chat and analysis do not automatically substitute a different provider.
Unavailable, incompatible, deprecated, authentication, timeout, and provider
failures are explicit terminal failures. Any future automatic fallback requires an
accepted equivalence/evaluation decision, bounded attempts, user disclosure, and
requested-versus-actual audit/metrics attribution.

Persist and expose requested and actual stable model/provider separately, along
with fallback flag/reason, prompt version, and execution-path identity. Historical
messages and immutable analysis runs keep their original actual attribution; a
selector affects only a future turn/run. Provider-neutral chat messages remain the
persistence boundary.

Embeddings remain fixed at Google `gemini-embedding-001`, 1536 dimensions. Changing
that requires a separate ADR, re-embedding/index migration, cost estimate, and
rollback. Deterministic risk/redline steps do not acquire a misleading selector.

## Alternatives considered

- Frontend-owned lists: rejected because capability and configuration drift cannot
  be safely validated in the browser.
- Preserve Gemini-first fallback: rejected because plausible legal output would
  conceal provider/path failure and lacks evaluated equivalence.
- Make embeddings selectable: rejected because stored vectors and indexes have a
  fixed compatibility contract.
- Infer actual provider from the requested label: rejected; actual identity is
  recorded at the executed server model boundary.

## Consequences

Provider outages are visible instead of masked. Users see fewer choices when local
credentials are absent. Adding/retiring a model is a server registry change with
tests and provider-document verification. Current actual attribution identifies the
configured/executed client but is not independent cryptographic provider-response
attestation. Credential presence also does not prove a project is entitled to every
model on that provider; a provider-side 404/auth/quota failure remains explicit and
must be covered by release smoke checks. Multi-model analysis, if introduced, requires per-step attribution
rather than a false single-model summary.

## Verification and observability

Tests cover stable-to-API mapping, configuration, workflow compatibility,
deprecation, production allowance, exact chat/analysis routing, Celery propagation,
requested/actual persistence/restoration, model switching, fixed embeddings,
explicit provider failure, and Output Guard. Live verification is provider-by-
provider and must be labeled separately from mocked adapter evidence.

## Rollout and rollback

The API additions are backward-compatible and attribution fields are nullable for
legacy records. Rollback must not relabel history or restore silent fallback.
Production release requires configured-provider smoke checks, cost/quota review,
and e2-micro build/runtime validation. This ADR does not authorize deployment.
