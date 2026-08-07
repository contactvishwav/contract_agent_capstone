# Contract Intelligence Agent - Architecture & Design

## Overview

The Contract Intelligence Agent is an agentic GraphRAG system designed to process and analyze commercial contracts from the CUAD (Contract Understanding Atticus Dataset). The system combines knowledge graph technology with large language models to provide intelligent querying and analysis of legal contract information.

## System Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │    Backend      │    │   Neo4j Graph   │
│   (React/TS)    │◄──►│   (FastAPI)     │◄──►│   Database      │
│                 │    │                 │    │                 │
│ • Chat UI       │    │ • LangGraph     │    │ • Contract Data │
│ • Model Select  │    │ • Agent Manager │    │ • Relationships │
│ • Streaming     │    │ • Tools         │    │ • Embeddings    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   LLM Providers │
                    │                 │
                    │ • OpenAI        │
                    │ • Google        │
                    │ • Anthropic     │
                    │ • Mistral       │
                    └─────────────────┘
```

## Technology Stack

### Backend
- **Framework**: FastAPI (Python 3.12+)
- **Agent Framework**: LangGraph for orchestrating AI agents
- **LLM Integration**: LangChain with multiple provider support
- **Database**: Neo4j Graph Database
- **Embeddings**: Google Generative AI Embeddings (`gemini-embedding-001`, 1536-dimensional)
- **Environment**: Docker containerized

### Frontend
- **Framework**: React 19 with TypeScript
- **Build Tool**: Vite
- **Styling**: Tailwind CSS v4
- **UI Components**: Radix UI primitives
- **State Management**: React Context API
- **Streaming**: Server-Sent Events (SSE)

### Infrastructure
- **Containerization**: Docker & Docker Compose
- **Database**: Neo4j (cloud-hosted demo instance)
- **Development**: Hot reload with file watching

## Core Components

### 1. Agent System (`backend/agent.py`)

The heart of the system is a LangGraph-powered agent that:
- Uses a conversational interface for contract queries
- Integrates with the ContractSearchTool for data retrieval
- Maintains conversation context and history
- Provides management-level explanations of technical results

**Key Features:**
- System message optimized for non-technical users
- Tool binding for contract search capabilities
- Conditional routing between assistant and tools
- Date-aware responses

### 2. Agent Manager (`backend/agent_manager.py`)

Manages multiple LLM providers and their configurations:
- **OpenAI**: GPT-4o
- **Google**: Gemini 1.5 Pro, Gemini 2.0 Flash
- **Anthropic**: Claude 3.5 Sonnet
- **Mistral**: Mistral Large

Dynamic initialization based on available API keys.

### 3. Contract Search Tool (`backend/tools/contract_search_tool.py`)

Advanced contract querying capabilities with:

**Search Parameters:**
- Date ranges (effective/end dates)
- Contract types (20+ categories)
- Parties involved
- Monetary values with operators
- Governing law by location
- Active/inactive status
- Semantic search via embeddings

**Advanced Features:**
- Custom Cypher query support for complex analytics
- Vector similarity search for semantic matching, via a real Neo4j native
  vector index (`db.index.vector.queryNodes`), not a brute-force scan
- Aggregation capabilities for business intelligence
- Relationship-based filtering

**Optional second-stage re-ranking** (`backend/agents/reranker_service.py`,
document and clause search levels, gated by `RERANKING_ENABLED` -
default off): retrieves a wider candidate pool (30) from the vector
index, then one batched Gemini structured-output call jointly scores
query+candidate relevance and returns the top 10 in re-ranked order - a
cross-encoder-style joint judgment is more accurate than cosine
similarity's independent-encoding order alone. A local cross-encoder
(sentence-transformers) was evaluated and rejected for this deployment:
measured directly, loading `cross-encoder/ms-marco-MiniLM-L-6-v2` and
running one batched 30-candidate inference cost ~209MB real RSS on top
of torch's own ~300MB+ import overhead - against the production
e2-micro VM's already-committed ~844MB of 1GB (see `docs/DEPLOYMENT.md`),
this doesn't fit without real OOM risk. Gemini adds no incremental
deployed memory and reuses the same circuit-breaker/timeout/caching
infrastructure already protecting every other Gemini call in this
codebase; on any failure (timeout, circuit open, malformed response) it
falls back to the original unranked order rather than failing the
request. See `docs/CAPSTONE_SUMMARY.md` for the full build/verification
history.

### 4. Frontend Chat Interface

**Components:**
- `ChatProvider`: Context management and state
- `ChatInput`: Message input with model selection
- `ChatOutput`: Streaming message display
- `Message`: Individual message rendering

**Features:**
- Real-time streaming responses
- Model switching during conversation
- Tool call visualization
- Conversation history persistence

## Data Model

### Neo4j Graph Schema

```cypher
// Core entities
(Contract)-[:HAS_GOVERNING_LAW]->(Country)
(Contract)<-[:PARTY_TO {role: string}]-(Party)
(Party)-[:LOCATED_IN]->(Country)

// Contract properties
Contract {
  file_id: string,
  summary: string,
  contract_type: string,
  contract_scope: string,
  effective_date: date,
  end_date: date,
  total_amount: float,
  embedding: vector
}
```

### Contract Types Supported
- Affiliate Agreement, Development, Distributor
- Endorsement, Franchise, Hosting, IP
- Joint Venture, License Agreement, Maintenance
- Manufacturing, Marketing, Non Compete/Solicit
- Outsourcing, Promotion, Reseller, Service
- Sponsorship, Strategic Alliance, Supply, Transportation

## API Design

### Streaming Architecture

The system uses Server-Sent Events (SSE) for real-time communication:

```typescript
POST /run/
{
  model: string,
  prompt: string,
  history: string // JSON array of message objects
}
```

**Response Stream Types:**
- `tool_call`: Tool invocation details
- `tool_message`: Tool execution results
- `ai_message`: LLM response chunks
- `user_message`: User input echo
- `history`: Updated conversation context
- `end`: Stream termination

### Message Format

Messages follow LangChain's message schema:
- `HumanMessage`: User inputs
- `AIMessage`: Assistant responses
- `ToolMessage`: Tool execution results

## Data Processing Pipeline

### CUAD Dataset Integration

The system processes the Contract Understanding Atticus Dataset through:

1. **Contract Extraction**: Parse 500+ legal contracts
2. **LLM Processing**: Extract structured data using language models
3. **Graph Construction**: Build relationships and entities
4. **Embedding Generation**: Create vector representations for semantic search
5. **Neo4j Import**: Populate graph database with processed data

### Research Components

Located in `/research/`:
- **Benchmarking**: Performance evaluation notebooks
- **Import Pipeline**: Data processing and graph construction
- **Demo Database**: Pre-populated Neo4j instance

## Deployment

### Docker Configuration

```yaml
services:
  backend:
    - FastAPI server on port 8000
    - Environment variables for LLM APIs and Neo4j
    - Hot reload for development
  
  ui:
    - React development server on port 5173
    - Proxy configuration for backend communication
    - Hot reload for frontend changes
```

### Environment Variables

**Backend:**
- Neo4j connection (URI, credentials, database)
- LLM API keys (OpenAI, Google, Anthropic, Mistral, Groq)

**Frontend:**
- Backend URL configuration

## Security Considerations

- API keys managed through environment variables
- CORS configured for development (restrict in production)
- Neo4j connection uses secure protocols (neo4j+s://)
- No sensitive data hardcoded in source

## Performance Optimizations

- **Streaming Responses**: Real-time user feedback
- **Vector Search**: Efficient semantic matching via Neo4j's native vector
  index (cosine similarity), with an optional Gemini re-ranking second
  stage over a wider candidate pool for document/clause search (default
  off - see "Contract Search Tool" above)
- **Connection Pooling**: Neo4j driver optimization
- **Caching**: Schema refresh disabled for performance
- **Lazy Loading**: LLM agents initialized on demand

## Development Workflow

1. **Setup**: Copy `.env.example` to `.env` and configure
2. **Start**: `docker-compose up` for full stack
3. **Development**: Hot reload enabled for both frontend and backend
4. **Testing**: Benchmark notebooks in `/research/benchmark/`

## Future Enhancements

This section previously listed Authentication and Monitoring as future
work - both are fully implemented and shipped (real JWT auth with RBAC,
org invites, TOTP MFA, and Google OIDC SSO in `backend/api/auth_api.py`/
`sso_api.py`; real Prometheus metrics, health checks, and LLM cost/
hallucination tracking in `backend/shared/monitoring/`) - see
`docs/CAPSTONE_SUMMARY.md` for the full build history. Removed rather than
left inaccurate, per this project's own documentation-accuracy discipline
(the same correction already applied to the README and the in-app
Documentation tab).

Genuinely still open:

- **Persistence**: Database storage for conversation history - Contract
  Chat's history is not currently persisted anywhere; confirmed absent,
  not just unlisted.
- **Scaling**: Multi-instance deployment support - a single-VM deployment
  (GCP e2-micro) by design, not a gap (`docs/CAPSTONE_SUMMARY.md` §10).
- **Additional Tools**: More specialized contract analysis capabilities.

## Key Design Decisions

1. **GraphRAG Architecture**: Combines knowledge graphs with retrieval-augmented generation
2. **Multi-LLM Support**: Flexibility to use different models based on requirements
3. **Streaming Interface**: Enhanced user experience with real-time responses
4. **Tool-Based Architecture**: Extensible system for adding new capabilities
5. **Containerized Deployment**: Simplified setup and deployment process