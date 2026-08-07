import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';

export const TechStackTab: React.FC = () => {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>System Architecture Overview</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div>
              <h3 className="font-semibold mb-3">Technology Stack</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span>Frontend:</span>
                  <Badge>React + TypeScript</Badge>
                </div>
                <div className="flex justify-between">
                  <span>Backend:</span>
                  <Badge>FastAPI + Python</Badge>
                </div>
                <div className="flex justify-between">
                  <span>AI Framework:</span>
                  <Badge>LangChain + LangGraph</Badge>
                </div>
                <div className="flex justify-between">
                  <span>Database:</span>
                  <Badge>Neo4j Aura + Vector Search</Badge>
                </div>
                <div className="flex justify-between">
                  <span>Embeddings:</span>
                  <Badge>Google text-embedding-004</Badge>
                </div>
                <div className="flex justify-between">
                  <span>LLM Providers:</span>
                  <Badge>Gemini, OpenAI, Claude</Badge>
                </div>
                <div className="flex justify-between">
                  <span>PDF Processing:</span>
                  <Badge>PyPDF2 + pdfplumber + OCR</Badge>
                </div>
              </div>
            </div>
            
            <div>
              <h3 className="font-semibold mb-3">Enhanced Frontend Components</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span>Search Interface:</span>
                  <Badge variant="outline">Multi-Level Search Selector</Badge>
                </div>
                <div className="flex justify-between">
                  <span>Clause Filters:</span>
                  <Badge variant="outline">41 CUAD Type Checkboxes</Badge>
                </div>
                <div className="flex justify-between">
                  <span>Section Filters:</span>
                  <Badge variant="outline">6 Section Type Categories</Badge>
                </div>
                <div className="flex justify-between">
                  <span>Results Display:</span>
                  <Badge variant="outline">Multi-Level Result Visualization</Badge>
                </div>
                <div className="flex justify-between">
                  <span>API Integration:</span>
                  <Badge variant="outline">Enhanced Search Service</Badge>
                </div>
              </div>
            </div>
          </div>

          <div className="border-t pt-6">
            <h3 className="font-semibold mb-3">Enhanced Search Capabilities</h3>
            <div className="bg-slate-50 rounded-lg p-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div className="flex items-center space-x-2">
                  <span>📄 Document-Level Search</span>
                </div>
                <div className="flex items-center space-x-2">
                  <span>📊 Section-Level Search</span>
                </div>
                <div className="flex items-center space-x-2">
                  <span>🤖 Clause-Level Search (41 CUAD Types)</span>
                </div>
                <div className="flex items-center space-x-2">
                  <span>👥 Relationship-Level Search</span>
                </div>
              </div>
              <div className="mt-4 pt-4 border-t border-slate-200">
                <div className="flex items-center justify-center space-x-4 text-xs text-slate-600">
                  <span>1536-dimensional embeddings (Gemini gemini-embedding-001)</span>
                  <span>•</span>
                  <span>Cosine similarity matching via Neo4j native vector index</span>
                </div>
              </div>
              {/* Re-ranking: real, built, gated behind RERANKING_ENABLED
                  (default off) - a genuine second stage on top of vector
                  search, not vector search itself, so described as its own
                  optional capability rather than folded into the bullets
                  above. See docs/CAPSTONE_SUMMARY.md for the full build
                  history, including why Gemini was chosen over a local
                  cross-encoder for this specific e2-micro deployment. */}
              <div className="mt-4 pt-4 border-t border-slate-200 text-sm">
                <div className="flex items-center space-x-2 mb-1">
                  <span>🎯 Optional re-ranking (document &amp; clause levels)</span>
                </div>
                <p className="text-xs text-slate-600">
                  A wider candidate pool (30) is retrieved from the vector index, then one
                  batched Gemini call jointly scores query+candidate relevance and re-orders
                  the top 10 - more accurate than similarity order alone, since a cross-encoder
                  judges the query and document together rather than independently. Off by
                  default (<code>RERANKING_ENABLED</code>); falls back to the original vector-search
                  order on any failure, so search never breaks because re-ranking did.
                </p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};