import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/shared/ui/card';
import { Badge } from '../components/shared/ui/badge';
import { ArrowRight, Brain, Shield, CheckCircle, Zap, Users, FileText, Bot, Target, Star, TrendingUp } from 'lucide-react';

export const USPPage: React.FC = () => {
  const [expandedUSPs, setExpandedUSPs] = useState<Set<string>>(new Set());

  const toggleUSP = (uspId: string) => {
    const newExpanded = new Set(expandedUSPs);
    if (newExpanded.has(uspId)) {
      newExpanded.delete(uspId);
    } else {
      newExpanded.add(uspId);
    }
    setExpandedUSPs(newExpanded);
  };

  const uniqueSellingPoints = [
    {
      id: 'multi_level_search',
      title: 'Multi-Level Semantic Search',
      icon: <Zap className="w-6 h-6" />,
      color: 'bg-blue-600',
      description: 'Industry-first 4-level hierarchical search across documents, sections, clauses, and relationships',
      differentiators: [
        { name: 'Document-Level Search', description: 'Full contract semantic matching', icon: <FileText className="w-5 h-5" /> },
        { name: 'Section-Level Search', description: 'Granular section-based retrieval', icon: <Bot className="w-5 h-5" /> },
        { name: 'Clause-Level Search', description: '41 CUAD clause types with embeddings', icon: <CheckCircle className="w-5 h-5" /> },
        { name: 'Relationship-Level Search', description: 'Party and entity relationship mapping', icon: <Users className="w-5 h-5" /> }
      ],
      competitors: 'Most competitors offer only document-level search without granular clause or relationship analysis'
    },

    {
      id: 'autonomous_planning',
      title: 'Query-Complexity-Based Execution Planning',
      icon: <Target className="w-6 h-6" />,
      color: 'bg-green-600',
      description: 'PlanExecutionEngine scores each query for complexity and intent, then picks one of four step sequences and validates the plan before running it',
      differentiators: [
        { name: 'Query Complexity Scoring', description: 'Keyword-based complexity/intent analysis selects Simple, Complex, Risk-Focused, or Compliance-Focused', icon: <Brain className="w-5 h-5" /> },
        { name: 'Dependency-Aware Step Execution', description: 'Each step declares its dependencies and runs only once they complete, with per-step timeout and retry', icon: <Zap className="w-5 h-5" /> },
        { name: 'Plan Self-Validation', description: 'Checks for missing dependencies, lowers confidence for very complex queries, adds an extra validation step for risk-focused ones', icon: <TrendingUp className="w-5 h-5" /> },
        { name: 'Four Execution Strategies', description: 'Simple/Complex/Risk-Focused/Compliance-Focused plans, each a different step sequence', icon: <Target className="w-5 h-5" /> }
      ],
      competitors: 'Most solutions run the exact same fixed step sequence for every query, regardless of what the query actually asks'
    },
    {
      id: 'cuad_integration',
      title: 'Complete CUAD Dataset Integration',
      icon: <FileText className="w-6 h-6" />,
      color: 'bg-orange-600',
      description: 'Full implementation of 41 CUAD clause types with confidence scoring and position tracking',
      differentiators: [
        { name: '41 CUAD Clause Types', description: 'Complete coverage of legal contract elements', icon: <CheckCircle className="w-5 h-5" /> },
        { name: 'Confidence Scoring', description: 'Reliability metrics for each extracted clause', icon: <Star className="w-5 h-5" /> },
        { name: 'Position Tracking', description: 'Source location and context preservation', icon: <Target className="w-5 h-5" /> },
        { name: 'Hierarchical Embeddings', description: 'Multi-level semantic representations', icon: <Zap className="w-5 h-5" /> }
      ],
      competitors: 'Competitors typically support 10-15 clause types without comprehensive CUAD integration'
    },

    {
      id: 'graph_database',
      title: 'Graph Database Intelligence',
      icon: <Users className="w-6 h-6" />,
      color: 'bg-teal-600',
      description: 'Native graph relationships unlock contract insights impossible with traditional databases',
      differentiators: [
        { name: 'Relationship Discovery', description: 'Find connected contracts, parties, and clause patterns', icon: <Users className="w-5 h-5" /> },
        { name: 'Contract Portfolio Analysis', description: 'Cross-contract risk aggregation and trend analysis', icon: <TrendingUp className="w-5 h-5" /> },
        { name: 'Precedent Lookup', description: 'Similar clause identification across contract history', icon: <Target className="w-5 h-5" /> },
        { name: 'Entity Relationship Mapping', description: 'Party networks, subsidiary connections, vendor relationships', icon: <Brain className="w-5 h-5" /> }
      ],
      competitors: 'Competitors use flat databases missing relationship intelligence and cross-contract insights'
    },

  ];

  const competitiveAdvantages = [
    {
      title: '4.7x Faster Semantic Search',
      description: 'Native Neo4j vector indexes vs. brute-force scan, benchmarked on 5,000 contract nodes (7x at the median)',
      icon: <Zap className="w-5 h-5 text-yellow-600" />,
      color: 'border-yellow-200 bg-yellow-50'
    },
    {
      title: 'Cross-Contract Risk Intelligence',
      description: 'Portfolio-level risk aggregation and insights',
      icon: <Users className="w-5 h-5 text-teal-600" />,
      color: 'border-teal-200 bg-teal-50'
    },
    {
      title: '0.75 F1 on Contract Metadata',
      description: 'CUAD-benchmarked extraction accuracy, up to 0.99 on parties and document name',
      icon: <Target className="w-5 h-5 text-red-600" />,
      color: 'border-red-200 bg-red-50'
    },
    {
      title: 'Complete Legal Compliance',
      description: '41 CUAD clause types with audit trails',
      icon: <Shield className="w-5 h-5 text-green-600" />,
      color: 'border-green-200 bg-green-50'
    },
    {
      title: 'Relationship Discovery',
      description: 'Find connected parties and contract patterns',
      icon: <Brain className="w-5 h-5 text-purple-600" />,
      color: 'border-purple-200 bg-purple-50'
    },
    {
      title: 'Circuit Breaker Protection',
      description: 'Redis-backed breakers around Gemini and Neo4j calls fail fast after repeated errors instead of stacking up timeouts',
      icon: <CheckCircle className="w-5 h-5 text-blue-600" />,
      color: 'border-blue-200 bg-blue-50'
    }
  ];

  return (
    <div className="space-y-8">
      <div className="text-center bg-white rounded-lg p-8 shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold text-slate-800 mb-3">Business Benefits</h1>
        <p className="text-lg text-slate-600 max-w-3xl mx-auto">
          Discover what makes our Contract Intelligence Agent superior to competitors through 
          advanced AI architecture, enterprise reliability, and comprehensive contract analysis.
        </p>
      </div>

      {/* Unique Selling Points */}
      <div className="space-y-6">
        <h2 className="text-2xl font-bold text-slate-800">Core Differentiators</h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {uniqueSellingPoints.map((usp) => (
            <Card 
              key={usp.id}
              className={`cursor-pointer transition-all duration-200 hover:shadow-lg ${
                expandedUSPs.has(usp.id) ? 'ring-2 ring-blue-500' : ''
              }`}
              onClick={() => toggleUSP(usp.id)}
            >
              <CardHeader className="pb-3">
                <div className="flex items-center gap-3">
                  <div className={`p-2 rounded-lg ${usp.color} text-white`}>
                    {usp.icon}
                  </div>
                  <div>
                    <CardTitle className="text-lg">{usp.title}</CardTitle>
                    <p className="text-sm text-slate-600">{usp.description}</p>
                  </div>
                </div>
              </CardHeader>
              
              {expandedUSPs.has(usp.id) && (
                <CardContent className="border-t pt-4">
                  <div className="space-y-4">
                    <div>
                      <h4 className="font-semibold text-sm mb-3 text-slate-800">Key Features</h4>
                      <div className="space-y-3">
                        {usp.differentiators.map((diff, idx) => (
                          <div key={idx} className="flex items-center gap-3">
                            <div className="flex items-center gap-2 min-w-0 flex-1">
                              <div className="p-2 bg-slate-100 rounded-full">
                                {diff.icon}
                              </div>
                              <div className="min-w-0 flex-1">
                                <h5 className="font-semibold text-sm text-slate-800">{diff.name}</h5>
                                <p className="text-xs text-slate-600">{diff.description}</p>
                              </div>
                            </div>
                            {idx < usp.differentiators.length - 1 && (
                              <ArrowRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                    
                    <div className="bg-slate-50 rounded-lg p-3">
                      <h5 className="font-semibold text-xs text-slate-700 mb-1">Competitive Advantage</h5>
                      <p className="text-xs text-slate-600">{usp.competitors}</p>
                    </div>
                    
                    <div className="pt-2 border-t">
                      <Badge variant="secondary" className="text-xs">
                        Click to collapse details
                      </Badge>
                    </div>
                  </div>
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      </div>

      {/* Competitive Advantages */}
      <div className="space-y-6">
        <h2 className="text-2xl font-bold text-slate-800">Competitive Advantages</h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {competitiveAdvantages.map((advantage, idx) => (
            <Card key={idx} className={`border ${advantage.color} hover:shadow-md transition-shadow`}>
              <CardContent className="p-4">
                <div className="flex items-start gap-3">
                  {advantage.icon}
                  <div>
                    <h3 className="font-semibold text-sm text-slate-800 mb-1">{advantage.title}</h3>
                    <p className="text-xs text-slate-600">{advantage.description}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Market Position */}
      <Card className="border-slate-200">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Target className="w-5 h-5 text-green-600" />
            Market Position & Value Proposition
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-slate-50 rounded-lg p-4">
            <h3 className="font-semibold mb-3 text-slate-800">Why Choose Our Solution</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div>
                <h4 className="font-medium mb-2 text-slate-700">Measured Accuracy (CUAD benchmark, 497 contracts)</h4>
                <div className="space-y-1 text-xs text-slate-600">
                  <div>• 0.75 avg F1 on contract metadata (up to 0.99 on parties/document name)</div>
                  <div>• 0.44 avg F1 on the 36 risk-relevant clause categories (up from 0.32) after a targeted prompt/fallback-pass fix for the 20 weakest categories - 243/510 contracts re-measured so far, full corpus in progress (docs/EVALUATION.md §4c)</div>
                  <div>• Cross-contract risk aggregation and insights</div>
                  <div>• Complete audit trail compliance</div>
                  <div>• Portfolio-level relationship intelligence</div>
                </div>
              </div>
              <div>
                <h4 className="font-medium mb-2 text-slate-700">Measured Performance</h4>
                <div className="space-y-1 text-xs text-slate-600">
                  <div>• 4.7x mean / 7x median faster semantic search vs. brute-force scan</div>
                  <div>• 73% live cache-hit rate on LLM calls - cache hits return in single-digit ms vs. multi-second real calls</div>
                  <div>• Circuit breakers fail fast on repeated Gemini/Neo4j errors instead of stacking up timeouts</div>
                  <div>• Prevent contract risks before signing</div>
                  <div>• Minimize compliance violations</div>
                </div>
              </div>
            </div>
          </div>
          
          <div className="flex justify-center">
            <Badge variant="secondary" className="text-xs">
              Every number on this page is a real, reproducible measurement - not an estimate
            </Badge>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};