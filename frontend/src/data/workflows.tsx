import { FileText, MessageSquare, BarChart3, Zap, Upload, Bot, CheckCircle, Shield, Users, Brain, AlertTriangle, Edit3 } from 'lucide-react';

export interface WorkflowStep {
  agent: string;
  description: string;
  icon: React.ReactNode;
  tech: string;
  isNew?: boolean;
  isEnhanced?: boolean;
}

export interface Workflow {
  title: string;
  icon: React.ReactNode;
  description: string;
  steps: WorkflowStep[];
}

export const workflows: Record<string, Workflow> = {
  storage: {
    title: 'Document Upload & Dataset Storage',
    icon: <FileText className="w-8 h-8" />,
    description: 'Complete flow for storing uploaded documents in searchable dataset',
    steps: [
      { agent: 'Document Upload', description: 'PDF validation and preprocessing', icon: <Upload className="w-5 h-5" />, tech: 'FastAPI, Pydantic validation, file size limits' },
      { agent: 'PDF Processing Agent', description: 'Text extraction and OCR processing', icon: <FileText className="w-5 h-5" />, tech: 'PyPDF2, pdfplumber, Tesseract OCR' },
      { agent: 'Clause Extraction Agent', description: 'Extract 43 clause types (41 CUAD + 2 supplemental)', icon: <Bot className="w-5 h-5" />, tech: 'LangChain, Gemini/OpenAI LLMs, spaCy NLP' },
      { agent: 'Knowledge Graph Storage', description: 'Store in Neo4j with relationships', icon: <CheckCircle className="w-5 h-5" />, tech: 'Neo4j Aura, py2neo driver, Cypher queries' },
      { agent: 'Multi-Level Embedding Generation', description: 'Create hierarchical embeddings for semantic search', icon: <Zap className="w-5 h-5" />, tech: 'Google text-embedding-004, Neo4j vector indexing' },
      { agent: 'Dataset Integration', description: 'Add to searchable contract corpus with embeddings', icon: <BarChart3 className="w-5 h-5" />, tech: 'Neo4j graph storage, vector similarity indexing' }
    ]
  },
  production_storage: {
    title: 'Document Upload & Dataset Storage - Production',
    icon: <FileText className="w-8 h-8" />,
    description: 'Enhanced production flow with multi-tenancy, versioning, and data lineage',
    steps: [
      { agent: '🆕 Tenant Validation', description: 'Validate tenant access and isolation', icon: <Shield className="w-5 h-5" />, tech: 'Multi-tenant authentication, RLS policies', isNew: true },
      { agent: '📝 Document Upload', description: 'PDF validation with version tracking', icon: <Upload className="w-5 h-5" />, tech: 'FastAPI, version detection, tenant isolation', isEnhanced: true },
      { agent: '📝 PDF Processing Agent', description: 'Text extraction with lineage tracking', icon: <FileText className="w-5 h-5" />, tech: 'PyPDF2, processing lineage, confidence scoring', isEnhanced: true },
      { agent: '🆕 Bias Detection', description: 'Check for content bias and fairness', icon: <Users className="w-5 h-5" />, tech: 'Fairness algorithms, demographic analysis', isNew: true },
      { agent: 'Multi-Level Embedding Generation', description: 'Document, section, clause, relationship embeddings', icon: <Zap className="w-5 h-5" />, tech: 'Google text-embedding-004, hierarchical processing' },
      { agent: '🆕 Embedding Validation', description: 'Validate embedding quality and consistency', icon: <CheckCircle className="w-5 h-5" />, tech: 'Dimension checks, consistency validation', isNew: true },
      { agent: '📝 Clause Extraction Agent', description: 'Extract 43 clause types (41 CUAD + 2 supplemental) with validation', icon: <Bot className="w-5 h-5" />, tech: 'LangChain, confidence scoring, source citation, multi-model validation', isEnhanced: true },
      { agent: '🆕 Error Handling & Safety', description: 'Validate outputs and handle errors gracefully', icon: <AlertTriangle className="w-5 h-5" />, tech: 'Circuit breakers, hallucination detection, output validation, safety checks', isNew: true },
      { agent: '📝 Knowledge Graph Storage', description: 'Store with tenant isolation and versioning', icon: <CheckCircle className="w-5 h-5" />, tech: 'Neo4j tenant policies, version chains, lineage tracking', isEnhanced: true },
      { agent: '🆕 Analysis Results Storage', description: 'Store AI analysis with full provenance', icon: <BarChart3 className="w-5 h-5" />, tech: 'Analysis nodes, processing lineage, audit trail', isNew: true }
    ]
  },
  chat: {
    title: 'Contract Search & Chat',
    icon: <MessageSquare className="w-8 h-8" />,
    description: 'Natural language search and analysis of existing contracts',
    steps: [
      { agent: 'User Query', description: 'Natural language contract search', icon: <MessageSquare className="w-5 h-5" />, tech: 'React frontend, WebSocket connections' },
      { agent: 'Search Processing', description: 'Query analysis and vector search', icon: <Bot className="w-5 h-5" />, tech: 'LangChain query processing, similarity search' },
      { agent: 'Contract Retrieval', description: 'Semantic matching from Neo4j database', icon: <FileText className="w-5 h-5" />, tech: 'Neo4j graph traversal, vector similarity' },
      { agent: 'Response Generation', description: 'Contextual answer with contract references', icon: <CheckCircle className="w-5 h-5" />, tech: 'LangChain RAG, Gemini/Claude LLMs' }
    ]
  },
  search: {
    title: 'Enhanced Multi-Level Search',
    icon: <MessageSquare className="w-8 h-8" />,
    description: 'Advanced semantic search across document, section, clause, and relationship levels',
    steps: [
      { agent: 'Query Processing', description: 'Analyze search intent and level', icon: <Brain className="w-5 h-5" />, tech: 'Query analysis, intent classification' },
      { agent: 'Embedding Generation', description: 'Convert query to 768-dim vector', icon: <Zap className="w-5 h-5" />, tech: 'Google text-embedding-004 API' },
      { agent: 'Multi-Level Search', description: 'Search documents, sections, clauses, relationships', icon: <BarChart3 className="w-5 h-5" />, tech: 'Neo4j vector similarity, cosine distance' },
      { agent: 'Result Ranking', description: 'Rank and merge results by relevance', icon: <CheckCircle className="w-5 h-5" />, tech: 'Similarity scoring, result fusion' }
    ]
  },
  orchestration: {
    title: 'Embedding Orchestration Pipeline',
    icon: <Zap className="w-8 h-8" />,
    description: 'Multi-agent embedding generation and validation workflow',
    steps: [
      { agent: 'Document Embedding Agent', description: 'Generate document & section embeddings', icon: <FileText className="w-5 h-5" />, tech: 'Google text-embedding-004, hierarchical processing' },
      { agent: 'Clause Embedding Agent', description: 'Extract & embed 43 clause types (41 CUAD + 2 supplemental)', icon: <Bot className="w-5 h-5" />, tech: 'Pattern matching, confidence scoring' },
      { agent: 'Relationship Embedding Agent', description: 'Extract & embed party relationships', icon: <Users className="w-5 h-5" />, tech: 'Entity extraction, context embedding' },
      { agent: 'Embedding Validator', description: 'Validate quality & consistency', icon: <CheckCircle className="w-5 h-5" />, tech: 'Dimension checks, similarity validation' },
      { agent: 'Neo4j Storage', description: 'Store multi-level embeddings', icon: <BarChart3 className="w-5 h-5" />, tech: 'Graph database, vector indexing' }
    ]
  },
  production_orchestration: {
    title: 'Embedding Orchestration Pipeline - Production',
    icon: <Zap className="w-8 h-8" />,
    description: 'Enhanced production embedding pipeline with tenant isolation, versioning, and quality assurance',
    steps: [
      { agent: '🆕 Tenant Context Validation', description: 'Validate tenant access and embedding isolation', icon: <Shield className="w-5 h-5" />, tech: 'Multi-tenant validation, access control', isNew: true },
      { agent: '📝 Document Embedding Agent', description: 'Generate document & section embeddings with lineage', icon: <FileText className="w-5 h-5" />, tech: 'Google text-embedding-004, processing lineage, version tracking', isEnhanced: true },
      { agent: '🆕 Fine-Tuning Integration', description: 'Apply domain-specific fine-tuned embeddings', icon: <Brain className="w-5 h-5" />, tech: 'LoRA adapters, legal domain fine-tuning, model versioning', isNew: true },
      { agent: '📝 Clause Embedding Agent', description: 'Extract & embed 43 clause types (41 CUAD + 2 supplemental) with validation', icon: <Bot className="w-5 h-5" />, tech: 'Pattern matching, confidence scoring, source attribution', isEnhanced: true },
      { agent: '📝 Relationship Embedding Agent', description: 'Extract & embed party relationships with context', icon: <Users className="w-5 h-5" />, tech: 'Entity extraction, context embedding, relationship validation', isEnhanced: true },
      { agent: '🆕 Cross-Validation Engine', description: 'Multi-model embedding consistency checks', icon: <CheckCircle className="w-5 h-5" />, tech: 'Multi-model validation, consensus scoring, anomaly detection', isNew: true },
      { agent: '📝 Embedding Validator', description: 'Enhanced quality & consistency validation', icon: <CheckCircle className="w-5 h-5" />, tech: 'Dimension checks, similarity validation, drift detection', isEnhanced: true },
      { agent: '🆕 Embedding Lineage Tracker', description: 'Track embedding provenance and versioning', icon: <BarChart3 className="w-5 h-5" />, tech: 'Lineage tracking, version chains, audit trails', isNew: true },
      { agent: '📝 Neo4j Storage', description: 'Store multi-level embeddings with tenant isolation', icon: <BarChart3 className="w-5 h-5" />, tech: 'Graph database, vector indexing, tenant policies', isEnhanced: true }
    ]
  },
  analysis: {
    title: 'Contract Intelligence Analysis',
    icon: <BarChart3 className="w-8 h-8" />,
    description: 'Comprehensive multi-agent contract analysis workflow',
    steps: [
      { agent: 'Planning Agent', description: 'Create optimal execution plan', icon: <Brain className="w-5 h-5" />, tech: 'LangGraph workflows, strategy patterns' },
      { agent: 'Clause Extraction Agent', description: 'Extract key contract clauses', icon: <Bot className="w-5 h-5" />, tech: 'Named Entity Recognition, regex patterns' },
      { agent: 'Policy Compliance Agent', description: 'Check against company policies', icon: <Shield className="w-5 h-5" />, tech: 'Rule engine, policy templates, scoring' },
      { agent: 'Risk Assessment Agent', description: 'Calculate risk scores', icon: <AlertTriangle className="w-5 h-5" />, tech: 'Risk matrices, weighted scoring algorithms' },
      { agent: 'Redline Generation Agent', description: 'Generate improvement suggestions', icon: <Edit3 className="w-5 h-5" />, tech: 'Text diff algorithms, suggestion templates' }
    ]
  }
};