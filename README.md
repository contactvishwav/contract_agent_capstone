# Agentic GraphRAG on Commercial Contracts

This repository implements an enterprise-grade **Multi-Agent Contract Intelligence Platform** using LangGraph, Neo4j, and Gemini. It automates legal review, policy compliance checking, and risk assessment for commercial contracts (CUAD).

## ⚠️ The Problem

Enterprise contract review is traditionally a slow, manual, and error-prone process. Legal teams face several critical challenges:
- **Volume & Complexity**: Reviewing hundreds of multi-page contracts for specific clauses is time-consuming.
- **Inconsistency**: Manual reviews often miss subtle deviations from company policy or jurisdictional nuances.
- **Hidden Risks**: Identifying "merged" clauses or missing critical protections requires deep expertise and extreme focus.
- **Lack of Precedents**: Finding similar clauses across thousands of historical documents is nearly impossible without advanced search.

## 💡 The Solution

This platform solves these challenges by combining **Graph Databases** with **Agentic AI** to create a system that thinks and reasons like a legal expert:
- **Autonomous Review**: 11+ specialized agents work together to extract, validate, and risk-rate clauses automatically.
- **Graph-Based Intelligence**: Neo4j stores multi-level relationships (Document → Section → Clause), enabling the system to understand context, not just keywords.
- **Explainable AI**: Every decision includes a "Chain-of-Thought" reasoning trace and a quality grade (A-F), giving legal teams confidence in the output.
- **Advanced RAG**: Beyond simple search, the system performs precedent lookup and historical analysis to ensure consistency across the entire contract repository.

See blog for more: [Agentic GraphRAG for Commercial Contracts](https://towardsdatascience.com/agentic-graphrag-for-commercial-contracts/)

![](https://cdn-images-1.medium.com/max/800/1*R57-KUW9zvXhx5VucKEMLA.png)

## 🚀 Enterprise Features

- **Multi-Agent System Design**: Orchestrates specialized agents including PDF Processing, Planning, Clause Extraction, Policy Checking, and Risk Assessment.
- **Autonomous Execution Engine**: `PlanExecutionEngine` runs the real default analysis pipeline with honest, per-step `node_status` reporting - a failed step surfaces as `processing_complete: false` with the specific failing stage identified, not a fabricated clean result. (A separate Supervisor-pattern prototype with circuit-breaker/retry/quality-grading machinery existed earlier but was never actually wired into any live path - removed rather than left as disconnected dead code; see `docs/CAPSTONE_SUMMARY.md`.)
- **Autonomous Planning**: Dynamically generates and adopts execution strategies based on query complexity.
- **Multi-Level Semantic Search**: Contextual retrieval at document, section, clause, and relationship levels.
- **Policy Compliance Engine**: Automated violation detection against custom policy playbooks.
- **Model Context Protocol (MCP)**: Standardized interface for AI models to access the contract intelligence library with full data isolation and tracing.

## 🔗 Model Context Protocol (MCP)

This repository includes a standalone **MCP Server** that exposes our contract intelligence capabilities to any MCP-compatible AI client (like Claude Desktop).

### Available Tools:
- **`search_clause_library`**: Semantic search across ingested contract clauses for legal research.
- **`get_playbook_rule`**: Fetch specific corporate policies and compliance requirements by contract type.
- **`search_prior_approved_clauses`**: Find similar language and approval rates from historical contracts.
- **`fetch_contract_metadata`**: Retrieve structured contract-level data (Parties, Dates, Law).

### Security & Best Practices:
- **Hardened Multi-Tenancy**: Data isolation is enforced at the database query level using `tenant_id`.
- **Request Tracing**: Centralized logging system generates a unique `trace_id` for every request, propagated across all layers.
- **Privacy**: Zero integration with browser `console.log`; all logs are handled server-side for maximum security.

## 🧠 Advanced AI Patterns

- **ReACT Pattern**: Reasoning-Action-Observation cycles for iterative problem-solving in contract analysis.
- **Chain-of-Thought (CoT)**: Explicit step-by-step reasoning documentation for transparent AI decision-making.
- **Advanced RAG**: Sophisticated retrieval with precedent lookup and multi-level embedding matching.
- **Self-Reflection**: Inter-agent validation and recursive plan refinement.

## 🛠️ Technical Stack

- **AI Framework**: LangChain + LangGraph for agent workflows and orchestration.
- **Database**: Neo4j Aura with vector indexing for graph-based knowledge storage.
- **Embeddings**: **Gemini 1536-dimensional** high-precision vectors (`gemini-embedding-001`).
- **LLM Providers**: Optimized for Google Gemini 1.5 Pro/Flash, supporting OpenAI and Claude.
- **Backend/Frontend**: FastAPI (Async Python) and React + TypeScript with Vite.
- **Caching (Optional)**: Redis support for high-throughput production RAG (can be enabled via `.env`).

## 📄 About CUAD

The [Contract Understanding Atticus Dataset (CUAD)](https://www.atticusprojectai.org/cuad) consists of 500 contracts with annotations for 41 legal clauses. This project extends CUAD analysis to handle custom provisions and complex legal patterns.

## ⚙️ Setup and Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/karthikkv1981/contract_intelli_agent.git
   cd contract_intelli_agent
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

> [!NOTE]
> **Redis Usage**: Redis is currently disabled as it is considered overkill for the capstone/development phase. However, it can be easily enabled by setting `CACHE_ENABLED=true` to implement caching features before moving to production.

   For a complete list of configuration options, including timeouts and parallel processing settings, refer to the [.env.example](file:///.env.example) file.

3. **Start with Docker**:
   ```bash
   docker-compose up
   ```

4. **Initialize Metadata** (Optional):
   ```bash
   python scripts/update_contract_types.py
   ```

## 📈 Future Enhancements

While the current platform is a complete capstone implementation, several features are designed for future production scaling:

- **Distributed Caching**: Enable Redis to cache LLM responses and GraphRAG results, significantly reducing latency and API costs.
- **Async Batch Workflows**: Further decouple long-running extraction tasks using Celery or similar workers.
- **Multi-Tenant Isolation**: Implement logical isolation for multiple legal teams or organizations.
- **Enhanced Adaptive Learning**: Use historical feedback to fine-tune the risk assessment scoring system.

---
*Created as part of a Legal AI Capstone Project.*

5. **Run the MCP Server**:
   ```bash
   PYTHONPATH=. python3 backend/mcp_server.py
   ```

6. **Verify Installation**:
   ```bash
   PYTHONPATH=. python3 backend/tests/test_mcp_capabilities.py
   ```
