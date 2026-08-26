import { Bot, FileText, Shield, AlertTriangle, Edit3, Users, CheckCircle, BarChart3 } from 'lucide-react';
import React from 'react';

export interface Agent {
  id: string;
  name: string;
  icon: React.ReactNode;
  role: string;
  description: string;
  capabilities: string[];
  input: string;
  output: string;
  color: string;
}

export const agents: Agent[] = [
  {
    id: 'pdf-processing',
    name: 'PDF Processing Agent',
    icon: <FileText className="w-6 h-6" />,
    role: 'Document Ingestion',
    description: 'Extracts text and metadata from uploaded PDF contracts with enhanced multi-level processing',
    capabilities: ['PDF text extraction', 'OCR processing', 'Contract structure analysis', 'Metadata extraction', 'Multi-level embedding generation'],
    input: 'Raw PDF files',
    output: 'Structured contract text + metadata + embeddings',
    color: 'bg-blue-500'
  },
  {
    id: 'document-embedding',
    name: 'Document Embedding Agent',
    icon: <FileText className="w-6 h-6" />,
    role: 'Semantic Processing',
    description: 'Generates hierarchical embeddings for full documents and sections using Google AI',
    capabilities: ['Document-level embeddings', 'Section identification', 'Hierarchical processing', 'Semantic representation'],
    input: 'Contract text + metadata',
    output: 'Document & section embeddings (768-dim vectors)',
    color: 'bg-cyan-500'
  },
  {
    id: 'clause-extraction',
    name: 'Clause Extraction Agent',
    icon: <Bot className="w-6 h-6" />,
    role: 'Content Analysis',
    description: 'Identifies and extracts 43 clause types (the 41 CUAD categories plus Indemnification and Payment Terms) with embeddings and confidence scores',
    capabilities: ['41 CUAD + 2 supplemental clause types', 'Pattern matching', 'Confidence scoring', 'Clause embeddings', 'Position tracking'],
    input: 'Contract text',
    output: 'Structured clause data + embeddings + confidence scores',
    color: 'bg-green-500'
  },
  {
    id: 'relationship-embedding',
    name: 'Relationship Embedding Agent',
    icon: <Users className="w-6 h-6" />,
    role: 'Relationship Analysis',
    description: 'Extracts and embeds party relationships and governing law contexts',
    capabilities: ['Party role extraction', 'Governing law identification', 'Relationship context embedding', 'Entity linking'],
    input: 'Contract text + entities',
    output: 'Relationship embeddings + context metadata',
    color: 'bg-teal-500'
  },
  {
    id: 'policy-compliance',
    name: 'Policy Compliance Agent',
    icon: <Shield className="w-6 h-6" />,
    role: 'Compliance Validation',
    description: 'Validates contract clauses against company policies and regulations',
    capabilities: ['Company policy checking', 'Violation detection', 'Severity assessment', 'Compliance scoring'],
    input: 'Extracted clauses',
    output: 'Policy violations with severity levels',
    color: 'bg-orange-500'
  },
  {
    id: 'risk-assessment',
    name: 'Risk Assessment Agent',
    icon: <AlertTriangle className="w-6 h-6" />,
    role: 'Risk Analysis',
    description: 'Calculates overall contract risk scores and provides recommendations',
    capabilities: ['Risk score calculation', 'Critical issue identification', 'Recommendation generation', 'Risk level classification'],
    input: 'Clauses + Policy violations',
    output: 'Risk assessment with recommendations',
    color: 'bg-red-500'
  },
  {
    id: 'redline-generation',
    name: 'Redline Generation Agent',
    icon: <Edit3 className="w-6 h-6" />,
    role: 'Contract Optimization',
    description: 'Generates contract redline suggestions based on policy violations',
    capabilities: ['Redline text generation', 'Priority assignment', 'Justification creation', 'Alternative language suggestions'],
    input: 'Policy violations',
    output: 'Redline suggestions with priorities',
    color: 'bg-indigo-500'
  },
  {
    id: 'embedding-validator',
    name: 'Embedding Validation Agent',
    icon: <CheckCircle className="w-6 h-6" />,
    role: 'Quality Assurance',
    description: 'Validates embedding quality, consistency, and dimensional accuracy',
    capabilities: ['Dimension validation', 'Consistency checks', 'Duplicate detection', 'Quality scoring'],
    input: 'Generated embeddings',
    output: 'Validation results + quality metrics',
    color: 'bg-emerald-500'
  },
  {
    id: 'migration-agent',
    name: 'Database Migration Agent',
    icon: <BarChart3 className="w-6 h-6" />,
    role: 'Schema Management',
    description: 'Manages database schema upgrades and contract migration to enhanced embeddings',
    capabilities: ['Schema upgrades', 'Batch migration', 'Rollback support', 'Data integrity checks'],
    input: 'Migration commands',
    output: 'Migration status + statistics',
    color: 'bg-slate-500'
  }
];

// AI Agent Patterns Implementation Status
export interface AgentPattern {
  name: string;
  status: 'implemented' | 'partial' | 'missing';
  description: string;
  location?: string;
  justification?: string;
}

export const agentPatterns: AgentPattern[] = [
  {
    name: 'Tool-Using Agent Pattern',
    status: 'implemented',
    description: 'Specialized tools for contract analysis tasks',
    location: 'backend/agents/intelligence_tools.py, pdf_tools.py'
  },
  {
    name: 'Multi-Agent Orchestration',
    status: 'implemented',
    description: 'Coordinated workflow execution across multiple agents via a real LangGraph StateGraph, with a Redis-backed human-review pause/resume gate for HIGH/CRITICAL-risk contracts',
    location: 'backend/agents/contract_intelligence_agents.py'
  },
  {
    name: 'State Management Pattern',
    status: 'implemented',
    description: 'Shared workflow context and inter-agent data sharing',
    location: 'backend/agents/intelligence_state.py, pdf_state.py'
  },
  {
    name: 'Self-Reflection Pattern',
    status: 'missing',
    description: 'Explicit step-by-step self-assessment of an agent\'s own output before it\'s used downstream',
    justification: 'An earlier autonomous-planning orchestrator had a limited form of this (plan validation) but was retired - see docs/CAPSTONE_SUMMARY.md. The current CUAD Mitigation cascade validates its own output for structural consistency (validate_cuad_analysis), which is a narrower, single-stage form of this pattern, not a general one.'
  },
  {
    name: 'ReACT Pattern',
    status: 'missing',
    description: 'Reasoning-Action-Observation cycles for iterative problem solving',
    justification: 'Would enable agents to iteratively refine their approach based on intermediate results. Useful for complex contract analysis where initial extraction might miss nuances.'
  },
  {
    name: 'Chain-of-Thought Pattern',
    status: 'missing',
    description: 'Explicit step-by-step reasoning documentation',
    justification: 'Would improve transparency and debugging of agent decisions. Particularly valuable for explaining why certain clauses were flagged or risk scores calculated.'
  },
  {
    name: 'Memory/Advanced RAG Pattern',
    status: 'partial',
    description: 'Basic storage via Neo4j, missing advanced retrieval and context injection',
    location: 'backend/infrastructure/contract_repository.py',
    justification: 'Current implementation stores contracts but lacks sophisticated retrieval for similar contract analysis, precedent lookup, or contextual clause interpretation.'
  }
];

export const getAgentTools = (agentId: string): string => {
  const toolMappings: Record<string, string> = {
    'pdf-processing': 'PDFTextExtractorTool, ContractAnalyzerTool, DataValidatorTool',
    'document-embedding': 'GoogleGenerativeAIEmbeddings, DocumentEmbeddingStrategy, Neo4j Vector Storage',
    'clause-extraction': 'ClauseDetectorTool, PolicyCheckerTool, ContractAnalyzerTool',
    'relationship-embedding': 'RelationshipEmbeddingStrategy, Neo4j Graph Queries, Entity Extraction Tools',
    'policy-compliance': 'PolicyCheckerTool, CompanyPolicyRules, ViolationDetector',
    'risk-assessment': 'RiskCalculatorTool, PolicyViolationAnalyzer, RiskScoring',
    'redline-generation': 'RedlineGeneratorTool, PolicyTemplates, TextDiffAlgorithms',
    'embedding-validator': 'EmbeddingValidator, DimensionChecker, ConsistencyValidator',
    'migration-agent': 'Neo4jMigrator, SchemaUpgrader, BatchProcessor'
  };
  return toolMappings[agentId] || 'ContractSearchTool, EnhancedContractSearchTool';
};