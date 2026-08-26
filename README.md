# Agentic GraphRAG on Commercial Contracts

This repository implements a **Multi-Agent Contract Intelligence Platform** using Neo4j and Gemini, with a LangGraph `StateGraph` orchestrating the real, sole analysis pipeline: clause extraction → policy checking → risk assessment → a human-review gate for HIGH/CRITICAL-risk contracts (real Redis-backed pause/resume, not a UI-only warning) → CUAD mitigation (deviation detection, jurisdiction adaptation, precedent matching, self-validation) → redline generation. Every stage is independently tracked (`node_status`, a queryable audit trail, real per-run timing) and the completed run gets a real, computed A-F quality grade. It automates legal review, policy compliance checking, and risk assessment for commercial contracts (CUAD).

## ⚠️ The Problem

Enterprise contract review is traditionally a slow, manual, and error-prone process. Legal teams face several critical challenges:
- **Volume & Complexity**: Reviewing hundreds of multi-page contracts for specific clauses is time-consuming.
- **Inconsistency**: Manual reviews often miss subtle deviations from company policy or jurisdictional nuances.
- **Hidden Risks**: Identifying "merged" clauses or missing critical protections requires deep expertise and extreme focus.
- **Lack of Precedents**: Finding similar clauses across thousands of historical documents is nearly impossible without advanced search.

## 💡 The Solution

This platform solves these challenges by combining **Graph Databases** with **Agentic AI** to create a system that thinks and reasons like a legal expert:
- **Autonomous Review**: real, specialized agents/tools work together to extract, validate, and risk-rate clauses automatically - clause extraction, policy checking, risk calculation, CUAD mitigation, and redline generation, orchestrated as a real LangGraph `StateGraph` with a genuine human-in-the-loop pause for HIGH/CRITICAL risk (see `docs/DEMO_UNDERSTANDING.md` §4/§6 for the exact call chain).
- **Graph-Based Intelligence**: Neo4j stores multi-level relationships (Contract → Section → Clause), enabling the system to understand context, not just keywords.
- **Explainable AI**: Every clause carries a grounding badge (verified against the source text, not hallucinated), a stable `clause_id` for traceability, and - when a learned pattern applies - a plain-language explanation of how historical review outcomes adjusted its risk level and by how much confidence.
- **Advanced RAG**: Beyond simple search, the system performs precedent lookup and historical analysis to ensure consistency across the entire contract repository.

See blog for more: [Agentic GraphRAG for Commercial Contracts](https://towardsdatascience.com/agentic-graphrag-for-commercial-contracts/)

![](https://cdn-images-1.medium.com/max/800/1*R57-KUW9zvXhx5VucKEMLA.png)

## 🚀 Enterprise Features

- **Multi-Agent System Design**: Orchestrates specialized agents including PDF Processing, Chunking, Clause Extraction, Policy Checking, Risk Assessment, CUAD Mitigation, and Redline Generation.
- **Real LangGraph Orchestration**: the analysis pipeline is a genuine `StateGraph`, not a linear script - honest, per-step `node_status` reporting (a failed step surfaces as `processing_complete: false` with the specific failing stage identified, never a fabricated clean result), a real Redis-backed pause/resume at a `human_review_gate` node for HIGH/CRITICAL-risk contracts (an admin must explicitly approve before redlines are generated), and a deterministic A-F quality grade computed once per completed run from that run's own real telemetry (`node_status`, per-clause grounding/confidence, and CUAD Mitigation's own self-validation confidence) - see `backend/agents/run_quality_grader.py`.
- **CUAD Mitigation with self-validation**: a real Phase3→Phase2→Phase1 fallback cascade (deviation detection, jurisdiction adaptation, precedent matching) where every tier runs its own output back through a validator, surfaced in the real audit trail - not just "did the step run," but "does this step's own output look internally consistent."
- **Multi-Level Semantic Search**: Contextual retrieval at document, section, clause, and relationship levels.
- **Policy Compliance Engine**: Automated violation detection against custom policy playbooks.
- **Model Context Protocol (MCP)**: Authenticated in-process tools for Contract Chat, plus an explicitly local-only standalone development server. External production MCP authentication is not implemented.

## 🔗 Model Context Protocol (MCP)

This repository includes the same MCP tools in Contract Chat through an authenticated in-process bridge. A standalone stdio server is available only for explicitly opted-in local development; it is disabled in production until external principal-to-tenant authentication exists.

### Available Tools:
- **`search_clause_library`**: Semantic search across ingested contract clauses for legal research.
- **`get_playbook_rule`**: Fetch specific corporate policies and compliance requirements by contract type.
- **`search_prior_approved_clauses`**: Find similar language and approval rates from historical contracts.
- **`fetch_contract_metadata`**: Retrieve structured contract-level data (Parties, Dates, Law).

### Security & Best Practices:
- **Tenant boundary**: Contract Chat binds the JWT-derived tenant outside the generic tool argument bag and discards caller/model overrides. A mandatory `tenant_id` alone is not authentication; local standalone callers are trusted as OS/process operators and production standalone startup fails closed.
- **Request tracing**: the HTTP correlation ID is carried through the real in-process MCP call. Correlation IDs support traceability and never grant tenant access.
- **Privacy**: Zero integration with browser `console.log`; all logs are handled server-side for maximum security.

## 🧠 Advanced AI Patterns

- **Advanced RAG**: Sophisticated retrieval with precedent lookup and multi-level embedding matching.
- **Adaptive learning**: historical review decisions (`FeedbackCollector` → `PatternLearner` → `AdaptiveAnalyzer`) genuinely adjust a clause's risk level on later analyses, applied live inside the real CUAD Mitigation stage of the analysis graph.

(A ReACT/Chain-of-Thought pattern-analysis step, and later an autonomous-planning orchestrator (`PlanExecutionEngine`) that briefly ran as the default analysis path, both existed earlier and were removed - the former confirmed unreachable/non-reasoning dead code, the latter retired once the real LangGraph path's `human_review_gate` safety pause made it the only path with a genuine reason to be the default; PlanExecutionEngine had zero real callers anywhere in the frontend and never completed a single real analysis in production due to an unrelated bug. See `docs/CAPSTONE_SUMMARY.md`.)

## 🛠️ Technical Stack

- **AI Framework**: LangChain throughout; LangGraph orchestrates the real analysis pipeline (the sole path - see `docs/DEMO_UNDERSTANDING.md` §6), the PDF-processing agents, and Contract Chat.
- **Database**: Neo4j Aura with vector indexing for graph-based knowledge storage.
- **Embeddings**: **Gemini 1536-dimensional** high-precision vectors (`gemini-embedding-001`).
- **LLM Providers**: the authenticated server registry exposes only configured, workflow-compatible choices. Google Gemini (`gemini-2.5-pro`/`gemini-2.5-flash`, the production default), OpenAI (`gpt-4o`), Anthropic (`claude-sonnet-5`), and Mistral (`mistral-large-latest`, chat-only and development-only) are wired in `backend/llm_manager.py`; legal chat/analysis failures do not silently cross providers. See [`ADR-006`](docs/adr/006-server-model-registry-and-explicit-legal-failure.md).
- **Backend/Frontend**: FastAPI (Async Python) and React + TypeScript with Vite.
- **Caching**: Redis - deployed and **enabled by default**, not optional (see the note below); backs LLM/GraphRAG-result caching, the Celery broker, and shared cross-process usage/cost/hallucination counters.

## 📄 About CUAD

The [Contract Understanding Atticus Dataset (CUAD)](https://www.atticusprojectai.org/cuad) consists of 500 contracts with annotations for 41 legal clauses. This project extends CUAD analysis to handle custom provisions and complex legal patterns.

**Extraction accuracy** is benchmarked against real CUAD ground truth across all 41 clause categories (497-contract scale) - full per-category precision/recall/F1 tables, methodology, the quota-forced Flash/Flash-Lite model-substitution story, and root-cause analysis on the weaker categories are in [`docs/EVALUATION.md`](docs/EVALUATION.md).

## ⚙️ Setup and Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/contactvishwav/contract_agent_capstone.git
   cd contract_agent_capstone
   ```

2. **Configure Environment**:
   Create a `.env` file in the root directory and add the following keys:

   ```bash
   cp .env.example .env
   ```

   ### Environment Variables

   | Variable | Description | Default / Example |
   |----------|-------------|-------------------|
   | `NEO4J_URI` | Neo4j database connection URI | `neo4j+s://...` |
   | `NEO4J_USERNAME` | Neo4j database username | `neo4j` |
   | `NEO4J_PASSWORD` | Neo4j database password | (Your password) |
   | `GOOGLE_API_KEY` | Google API Key for Gemini models | (Your API key) |
   | `ENVIRONMENT` | Set to `production` for production deployments; otherwise `development` | `development` |
   | `VITE_BACKEND_URL` | The URL of the backend API (frontend uses this) | `http://localhost:8000` |
   | `PHOENIX_COLLECTOR_ENDPOINT` | Endpoint for Arize Phoenix performance tracing | `http://localhost:6006/v1/traces` |
   | `REDIS_URL` | Redis connection URL for caching | `redis://localhost:6379` |
   | `CACHE_ENABLED` | Enable/Disable Redis caching | `false` |
   | `MONITORING_ENABLED`| Enable/Disable performance monitoring | `true` |
   | `JWT_SECRET_KEY` | Signing secret for issued auth tokens (HS256) | falls back to a loudly-logged, insecure dev-only default - **must** be set for any real deployment |
   | `ENCRYPTION_KEY` | Key source for AES-256-GCM field-level encryption at rest | same dev-only-default caveat as `JWT_SECRET_KEY` |
   | `CORS_ALLOWED_ORIGINS` | Comma-separated allowed origins in production | permissive in dev; fails closed (empty) if unset in production |

> [!NOTE]
> **Redis Usage**: Redis is deployed and enabled by default (`docker-compose.yml`'s `CACHE_ENABLED` defaults to `true`) - it backs LLM-response/GraphRAG-result caching, the Celery task broker, and shared usage/cost counters across the backend and worker containers. Not optional infrastructure for this platform's real feature set.

> [!NOTE]
> **Credential provisioning (SSO/MFA/invites)** needs several more variables not in the table above (`GOOGLE_OAUTH_CLIENT_ID`/`SECRET`/`REDIRECT_URI`, `RESEND_API_KEY`, `INVITE_EMAIL_FROM`, `FRONTEND_BASE_URL`) - all present with real defaults in `.env.example`, with the full real-account setup (a real Google OAuth client, a real Resend account) documented in `docs/DEPLOYMENT.md`.

   For a complete list of configuration options, including timeouts, cache TTLs, and parallel processing settings, refer to the [.env.example](file:///.env.example) file - kept current with every real, currently-read env var in this codebase.

3. **Start with Docker**:
   ```bash
   docker-compose up
   ```
   > [!NOTE]
   > **This `docker-compose.yml` is dev-only** - every service builds the `dev` image target (hot-reload, dev dependencies), and there are no `restart:` policies or resource limits anywhere in the file. A real deployment needs its own compose file or orchestration manifest, not this file used as-is - `docker-compose.prod.yml` plus [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) is exactly that: a real, verified, currently-live deployment (GCP e2-micro + Neo4j AuraDB + Caddy/Let's Encrypt), not just a Kubernetes-manifest hand-wave.

4. **Open the app and create an account**: the frontend is served at `http://localhost:3000` (mapped from the `ui` container's Vite dev server). Every route requires a signed-in session - `POST /api/auth/register` is bootstrap-only (it creates the *first* user of a new tenant; joining an already-provisioned tenant needs a real admin-issued invite, or Google SSO against an invite, or MFA if the account has it enabled - see `docs/CAPSTONE_SUMMARY.md` §16/§17 for the full credential-provisioning story).

5. **Initialize Metadata** (Optional):
   ```bash
   python scripts/update_contract_types.py
   ```

6. **Run the local-only MCP Server** (the caller is a trusted local operator; do not expose this as production authentication):
   ```bash
   ENVIRONMENT=development MCP_STANDALONE_LOCAL_ONLY=true PYTHONPATH=. python3 backend/mcp_server.py
   ```

7. **Verify Installation**:
   ```bash
   PYTHONPATH=. python3 backend/tests/test_mcp_capabilities.py
   ```

## 🚀 Production Deployment

This isn't just a documented plan - it's a real, currently-live deployment: a GCP e2-micro VM (Always Free tier) behind Caddy for automatic Let's Encrypt TLS, Neo4j AuraDB Free for the database, and `GCPSecretManagerKeyProvider` for secrets. `docker-compose.prod.yml` (a separate file from the dev-only `docker-compose.yml` above) is what actually runs there. Full step-by-step instructions, real measured resource-fit numbers for the e2-micro's 1GB budget, and the update procedure are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md); the engagement history behind it (what was found live, what broke, how it was fixed) is in `docs/CAPSTONE_SUMMARY.md` §11/§12.

## ✅ Implemented Since the Original Capstone Scope

These four items were originally listed here as future work. All four are real, shipped, and tested - not prototypes. Full before/after evidence (file:line, commit hashes, test counts) is in [`docs/CAPSTONE_SUMMARY.md`](docs/CAPSTONE_SUMMARY.md) §6.

- **Distributed Caching**: Redis-backed caching for both LLM responses (extraction/policy-evaluation) and the real GraphRAG vector-search path, keyed on query + tenant_id + filters.
- **Async Batch Workflows**: Contract analysis runs as a real Celery task (`POST /analyze` enqueues and returns immediately; `GET /tasks/{task_id}/status` polls real task state) - not a synchronous request blocking for 20s+.
- **Multi-Tenant Isolation**: Real JWT-based authentication (`POST /api/auth/token`, bcrypt-hashed accounts via `POST /api/auth/register`) - `tenant_id`/role come exclusively from a validated, signed token, not a caller-supplied parameter. Proven with a genuine cross-tenant test: a valid token for one tenant cannot read another tenant's data no matter what it claims elsewhere in the request.
- **Enhanced Adaptive Learning**: Historical review decisions genuinely adjust a clause's risk level on later analyses - a real end-to-end feedback loop (`FeedbackCollector` → `PatternLearner` → `AdaptiveAnalyzer`), not a feedback button that goes nowhere. Surfaced in the UI with a plain-language explanation and confidence score whenever a learned pattern applies.

## 📈 Future Enhancements

Real, honestly-scoped remaining work - see `docs/CAPSTONE_SUMMARY.md` §9 for the full prioritized list, and `docs/DEMO_UNDERSTANDING.md` §8 for the specific engineering reason each item isn't built yet:

- Push the risk-category extraction benchmark further (9 of 36 categories still score below 0.30 F1, down from 20/36 after a root-caused fix pass - full tables and before/after numbers in [`docs/EVALUATION.md`](docs/EVALUATION.md) §4c, engagement history in `docs/CAPSTONE_SUMMARY.md` §4 item 12/§15).
- Password reset and email verification - the one remaining honest gap in the credential story below.
- Horizontal scaling infrastructure - a deliberate single-VM scope boundary for this capstone (see `docs/DEPLOYMENT.md`), not a gap in what's built.

~~Real credential provisioning (org invites, SSO, MFA) replacing today's self-service registration~~ - done: real org invites, real Google OIDC SSO, and real TOTP MFA (with backup codes and anti-replay) are all built, live-verified end to end, and documented in `docs/CAPSTONE_SUMMARY.md` §16/§17.

~~TLS/HTTPS termination, a Neo4j backup/DR story, and a real secrets manager - all blocked on a chosen deployment target~~ - a real target now exists: see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) (GCP e2-micro + Neo4j AuraDB Free, whose managed backups cover DR + Caddy/Let's Encrypt for TLS) and `GCPSecretManagerKeyProvider` (`backend/infrastructure/encryption.py`, `KEY_PROVIDER=gcp`) for GCP Secret Manager-backed encryption keys.

## 📚 Full Technical Narrative

- [`docs/DEMO_UNDERSTANDING.md`](docs/DEMO_UNDERSTANDING.md) - the single most complete technical reference: every AI/ML concept explained with real file:line grounding, the full request-lifecycle walkthrough, every deliberate tradeoff with its rationale, and honest answers to the hardest questions about this system's real weak points.
- [`docs/CAPSTONE_SUMMARY.md`](docs/CAPSTONE_SUMMARY.md) - the full engagement history, phase by phase, commit by commit.
- [`docs/EVALUATION.md`](docs/EVALUATION.md) - the standalone extraction-accuracy report (methodology, full per-category tables, known limitations).
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) - the real GCP deployment runbook.

---
*Created as part of a Legal AI Capstone Project.*
