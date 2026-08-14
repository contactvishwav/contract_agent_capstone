# Antigravity Progress & Audit Tracking - Enhanced Search

This document records the analysis, implementation decisions, progress, and verification results for fixing all audit items in Enhanced Search on branch `feat/persistent-chat-sessions`.

---

## Active Task Summary
- **Branch**: `feat/persistent-chat-sessions`
- **Focus**: Fixing Enhanced Search audit issues in strict priority order (CRITICAL -> MODERATE -> MINOR).
- **Verifications**: Unit tests + Real Playwright browser verification with screenshots/DOM state evidence.
- **Scope Restriction**: No citation/highlight click-through for Enhanced Search results in this pass (explicitly out of scope).

---

## Audit Items & Status Tracker

### CRITICAL (Fix First)
- [ ] **Item 1: Wire real, embeddings-generating enhanced upload path into product UI**
  - *Location*: `frontend/src/components/features/contracts/DocumentUpload.tsx`, `frontend/src/services/enhancedSearchApi.ts`
  - *Details*: Add user-visible control (toggle/checkbox for Multi-Level Embeddings) in `DocumentUpload.tsx`. Call `enhancedSearchApi.uploadEnhancedDocument` when enabled.
- [ ] **Item 2: Fix "All Levels" search's plural/singular key mismatch**
  - *Location*: `backend/application/services/enhanced_search_service.py`, `backend/shared/utils/search_mapper.py`, `frontend/src/components/features/search/EnhancedSearchResults.tsx`
  - *Details*: `_search_all_levels` backend produces singular keys (`document`, `section`, `clause`, `relationship`), but `EnhancedSearchResults.tsx` reads plural keys (`documents`, `sections`, `clauses`, `relationships`). Align keys so "All Levels" results render properly. Add Playwright E2E test.
- [ ] **Item 3: Fix Clause-level search encryption mismatch & SearchResponseMapper error surfacing**
  - *Location*: `backend/application/services/enhanced_document_processing_service.py`, `backend/shared/utils/search_strategies.py`, `backend/shared/utils/search_mapper.py`, `frontend/src/components/features/search/EnhancedSearchResults.tsx`, `EnhancedSearch.tsx`
  - *Details*:
    - Enhanced processing service stored `cl.content` as plaintext, while `ClauseSearchStrategy` unconditionally called `field_encryptor.decrypt()`, triggering `DecryptionError`. Update enhanced processing service to redact PII and encrypt `cl.content` with `field_encryptor.encrypt()` at rest.
    - Update `SearchResponseMapper` to check `result.search_metadata.get("error")` and set `success: false` with the error message instead of hardcoding `success: true`.
    - Update frontend search UI to read and surface `error` / `success: false` states distinctly from empty results.

---

### MODERATE (Fix Second)
- [ ] **Item 4: Fix embedding-status endpoint checking real vector fields (`Contract.embedding`)**
  - *Location*: `backend/api/enhanced_document_upload.py`
  - *Details*: Update `/api/documents/enhanced/embedding-status/{contract_id}` to check `c.embedding IS NOT NULL as has_document_embedding`.
- [ ] **Item 5: Add rate limiting to Enhanced Search endpoints**
  - *Location*: `backend/api/enhanced_contract_search.py`
  - *Details*: Apply tenant-scoped rate limiting matching `/api/run/` pattern.
- [ ] **Item 6: Fix "All Levels" dropping filters**
  - *Location*: `backend/application/services/enhanced_search_service.py`, `backend/shared/utils/enhanced_contract_search_tool.py`
  - *Details*: Forward `contract_type`, `active`, and date range filters (`min_effective_date`, etc.) to sub-searches in `_search_all_levels`.
- [ ] **Item 7: Fix relationship embeddings attachment in enhanced pipeline**
  - *Location*: `backend/application/services/enhanced_document_processing_service.py`
  - *Details*: Root cause: `_store_enhanced_embeddings` attempted Cypher match `{name: $party_name, tenant_id: $tenant_id}` on `p:Party`, but `Party` nodes do not store `tenant_id` (the `c:Contract` node stores `tenant_id`). Fix Cypher match to properly link relationship embeddings to `(p:Party)-[r:PARTY_TO]->(c:Contract)`.

---

### MINOR (Fix Last)
- [ ] **Item 8: Remove raw debug string on empty results**
  - *Location*: `frontend/src/components/features/search/EnhancedSearchResults.tsx`
  - *Details*: Remove `"Debug: Received object - []"` text from user-facing empty state.
- [ ] **Item 9: Fix enhanced-uploaded contracts losing filename in document list**
  - *Location*: `backend/api/enhanced_document_upload.py`, `frontend/src/pages/IntelligencePage.tsx`
  - *Details*: Preserve original filename in contract creation and history context.

---

## Execution Log & Verification Proofs
*(Will be updated after each item fix)*
