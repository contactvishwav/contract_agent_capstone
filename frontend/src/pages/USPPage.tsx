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
      title: 'Autonomous Planning & Reasoning',
      icon: <Target className="w-6 h-6" />,
      color: 'bg-green-600',
      description: 'Self-adapting AI that analyzes query complexity and creates optimal execution strategies',
      differentiators: [
        { name: 'Query Complexity Analysis', description: 'Automatic strategy selection based on requirements', icon: <Brain className="w-5 h-5" /> },
        { name: 'Dynamic Workflow Construction', description: 'LangGraph-based adaptive execution paths', icon: <Zap className="w-5 h-5" /> },
        { name: 'Self-Reflection & Learning', description: 'Feedback adaptation for continuous improvement', icon: <TrendingUp className="w-5 h-5" /> },
        { name: 'Multi-Strategy Execution', description: 'Simple/Complex/Risk/Compliance-focused approaches', icon: <Target className="w-5 h-5" /> }
      ],
      competitors: 'Most solutions use static workflows without intelligent planning or self-adaptation capabilities'
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
      title: '80% Faster Contract Review',
      description: 'Automated analysis reduces manual review time',
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
      title: '95% Risk Detection Accuracy',
      description: 'AI-powered identification of contract risks',
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
      title: 'Enterprise Reliability',
      description: '99.9% uptime with circuit breaker protection',
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
                <h4 className="font-medium mb-2 text-slate-700">Business Impact</h4>
                <div className="space-y-1 text-xs text-slate-600">
                  <div>• 80% reduction in contract review time</div>
                  <div>• Cross-contract risk aggregation and insights</div>
                  <div>• 95% accuracy in risk detection</div>
                  <div>• Complete audit trail compliance</div>
                  <div>• Portfolio-level relationship intelligence</div>
                </div>
              </div>
              <div>
                <h4 className="font-medium mb-2 text-slate-700">ROI & Cost Savings</h4>
                <div className="space-y-1 text-xs text-slate-600">
                  <div>• Reduce legal review costs by 80%</div>
                  <div>• Prevent contract risks before signing</div>
                  <div>• Accelerate deal closure timelines</div>
                  <div>• Minimize compliance violations</div>
                  <div>• Optimize contract portfolio performance</div>
                </div>
              </div>
            </div>
          </div>
          
          <div className="flex justify-center">
            <Badge variant="secondary" className="text-xs">
              Delivering measurable ROI through intelligent contract automation
            </Badge>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};