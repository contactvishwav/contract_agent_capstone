# Source-of-truth map

This index assigns authority; it does not duplicate the underlying documents. When a document conflicts with validated code/runtime evidence, record the discrepancy in `docs/SYSTEM_MAP.md` and resolve it through a focused change or ADR.

| Concern | Governing source | Supporting/history only |
|---|---|---|
| Agent conduct, invariants, Git ownership | [`../AGENTS.md`](../AGENTS.md), [`WORKING_PROTOCOL.md`](WORKING_PROTOCOL.md) | Chat transcripts |
| Verified current system paths and gaps | [`SYSTEM_MAP.md`](SYSTEM_MAP.md), current code/tests | [`ARCHITECTURE.md`](ARCHITECTURE.md) is partially stale |
| Default/fallback analysis paths | `backend/agents/contract_intelligence_agents.py`, `backend/agents/planning/`, accepted ADR when created | [`DEMO_UNDERSTANDING.md`](DEMO_UNDERSTANDING.md) §4/§6, [`CAPSTONE_SUMMARY.md`](CAPSTONE_SUMMARY.md) §14 |
| Contract Chat | `backend/main.py`, `backend/contract_chat_agent.py`, session repository/routes, frontend chat components, tests | [`ARCHITECTURE.md`](ARCHITECTURE.md) requires refresh |
| Search/RAG | `backend/shared/utils/search_strategies.py`, `enhanced_contract_search_tool.py`, vector config, tests | [`DEMO_UNDERSTANDING.md`](DEMO_UNDERSTANDING.md) §4.2/§7 |
| Authentication, tenancy, RBAC | `backend/governance/`, authenticated routes, tenant-isolation tests | [`CAPSTONE_SUMMARY.md`](CAPSTONE_SUMMARY.md) §16/§17 |
| Neo4j schema and migrations | `backend/migrations/run_all_migrations.py`, migration modules, repositories | [`Enterprise_Database_Design.md`](Enterprise_Database_Design.md) is descriptive, not migration authority |
| Redis/Celery/monitoring | `backend/celery_app.py`, `backend/tasks.py`, `backend/shared/cache`, `monitoring`, `reliability` | [`DEMO_UNDERSTANDING.md`](DEMO_UNDERSTANDING.md) |
| MCP | `backend/mcp_server.py`, `backend/mcp/`, MCP tests | README descriptions do not establish authentication |
| Extraction evaluation/product claims | [`EVALUATION.md`](EVALUATION.md) and raw `research/benchmark` artifacts | README summaries |
| Production deployment | [`DEPLOYMENT.md`](DEPLOYMENT.md), `docker-compose.prod.yml`, `Caddyfile` | Local compose behavior |
| Engagement history/roadmap | [`CAPSTONE_SUMMARY.md`](CAPSTONE_SUMMARY.md) | Never treat roadmap text as implemented behavior |
| Architecture decisions | Accepted records under [`adr/`](adr/) | Unaccepted ADR candidates are questions, not decisions |

Documentation precedence is defined in `AGENTS.md`. `README.md` is onboarding; `DEMO_UNDERSTANDING.md` is a technical narrative; `CAPSTONE_SUMMARY.md` is chronological history. None overrides current code, tests, migrations, evaluation artifacts, or accepted ADRs.
