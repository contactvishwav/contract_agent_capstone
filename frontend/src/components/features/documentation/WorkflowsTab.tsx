import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { ArrowRight } from 'lucide-react';

interface WorkflowStep {
  agent: string;
  description: string;
  tech: string;
  isNew?: boolean;
  isEnhanced?: boolean;
}

const workflows: Record<string, { title: string; description: string; steps: WorkflowStep[] }> = {
  storage: {
    title: 'Document Upload & Dataset Storage',
    description: 'Complete flow for storing uploaded documents in searchable dataset',
    steps: [
      { agent: 'Document Upload', description: 'PDF validation and preprocessing', tech: 'FastAPI, Pydantic validation, file size limits' },
      { agent: 'PDF Processing Agent', description: 'Text extraction and OCR processing', tech: 'PyPDF2, pdfplumber, Tesseract OCR' },
      { agent: 'Clause Extraction Agent', description: 'Extract 41 CUAD clause types', tech: 'LangChain, Gemini/OpenAI LLMs, spaCy NLP' },
      { agent: 'Knowledge Graph Storage', description: 'Store in Neo4j with relationships', tech: 'Neo4j Aura, py2neo driver, Cypher queries' },
      { agent: 'Multi-Level Embedding Generation', description: 'Create hierarchical embeddings for semantic search', tech: 'Google text-embedding-004, Neo4j vector indexing' },
      { agent: 'Dataset Integration', description: 'Add to searchable contract corpus with embeddings', tech: 'Neo4j graph storage, vector similarity indexing' }
    ]
  },
  production_storage: {
    title: 'Document Upload & Dataset Storage - Production',
    description: 'Enhanced production flow with multi-tenancy, versioning, and data lineage',
    steps: [
      { agent: '🆕 Tenant Validation', description: 'Validate tenant access and isolation', tech: 'Multi-tenant authentication, RLS policies', isNew: true },
      { agent: '📝 Document Upload', description: 'PDF validation with version tracking', tech: 'FastAPI, version detection, tenant isolation', isEnhanced: true },
      { agent: '📝 PDF Processing Agent', description: 'Text extraction with lineage tracking', tech: 'PyPDF2, processing lineage, confidence scoring', isEnhanced: true },
      { agent: '🆕 Bias Detection', description: 'Check for content bias and fairness', tech: 'Fairness algorithms, demographic analysis', isNew: true },
      { agent: 'Multi-Level Embedding Generation', description: 'Document, section, clause, relationship embeddings', tech: 'Google text-embedding-004, hierarchical processing' },
      { agent: '🆕 Embedding Validation', description: 'Validate embedding quality and consistency', tech: 'Dimension checks, consistency validation', isNew: true },
      { agent: '📝 Clause Extraction Agent', description: 'Extract 41 CUAD clause types with validation', tech: 'LangChain, confidence scoring, source citation, multi-model validation', isEnhanced: true },
      { agent: '🆕 Error Handling & Safety', description: 'Validate outputs and handle errors gracefully', tech: 'Circuit breakers, hallucination detection, output validation, safety checks', isNew: true },
      { agent: '📝 Knowledge Graph Storage', description: 'Store with tenant isolation and versioning', tech: 'Neo4j tenant policies, version chains, lineage tracking', isEnhanced: true },
      { agent: '🆕 Analysis Results Storage', description: 'Store AI analysis with full provenance', tech: 'Analysis nodes, processing lineage, audit trail', isNew: true }
    ]
  }
};

export const WorkflowsTab: React.FC = () => {
  const [activeWorkflows, setActiveWorkflows] = useState<Set<string>>(new Set());

  const toggleWorkflow = (key: string) => {
    const newActiveWorkflows = new Set(activeWorkflows);
    if (newActiveWorkflows.has(key)) {
      newActiveWorkflows.delete(key);
    } else {
      newActiveWorkflows.add(key);
    }
    setActiveWorkflows(newActiveWorkflows);
  };

  return (
    <div className="space-y-6">
      {Object.entries(workflows).map(([key, workflow]) => (
        <Card 
          key={key} 
          className={`overflow-hidden cursor-pointer transition-all duration-200 ${
            activeWorkflows.has(key) ? 'ring-2 ring-blue-500 shadow-lg' : 'hover:shadow-md'
          }`}
          onClick={() => toggleWorkflow(key)}
        >
          <CardHeader className="bg-slate-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="p-2 bg-blue-500 text-white rounded-lg">
                  📋
                </div>
                <div>
                  <CardTitle className="text-xl">{workflow.title}</CardTitle>
                  <p className="text-slate-600">{workflow.description}</p>
                </div>
              </div>
              <Badge variant="outline">Click to expand</Badge>
            </div>
          </CardHeader>
          {activeWorkflows.has(key) && (
            <CardContent className="p-6 border-t">
              <div className="flex items-center justify-between">
                {workflow.steps.map((step, idx) => (
                  <React.Fragment key={idx}>
                    <div className="flex flex-col items-center text-center max-w-32">
                      <div className="p-3 bg-slate-100 rounded-full mb-2 hover:bg-blue-100 transition-colors">
                        📄
                      </div>
                      <h4 className={`font-semibold text-sm mb-1 ${
                        step.isNew ? 'text-red-600' : 
                        step.isEnhanced ? 'text-red-600' : 
                        'text-slate-800'
                      }`}>
                        {step.agent}
                      </h4>
                      <p className="text-xs text-slate-600 mb-2">{step.description}</p>
                      <div className="text-xs text-blue-600 bg-blue-50 px-2 py-1 rounded border">
                        {step.tech}
                      </div>
                    </div>
                    {idx < workflow.steps.length - 1 && (
                      <ArrowRight className="w-5 h-5 text-slate-400 mx-2 animate-pulse" />
                    )}
                  </React.Fragment>
                ))}
              </div>
            </CardContent>
          )}
        </Card>
      ))}
    </div>
  );
};