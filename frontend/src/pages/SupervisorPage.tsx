import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../components/shared/ui/card';
import { Badge } from '../components/shared/ui/badge';
import { ArrowRight, Brain, Shield, CheckCircle, AlertTriangle, Zap, FileText, Bot, Radio } from 'lucide-react';

export const SupervisorPage: React.FC = () => {
  const [expandedFlows, setExpandedFlows] = useState<Set<string>>(new Set());

  const toggleFlow = (flowId: string) => {
    const newExpanded = new Set(expandedFlows);
    if (newExpanded.has(flowId)) {
      newExpanded.delete(flowId);
    } else {
      newExpanded.add(flowId);
    }
    setExpandedFlows(newExpanded);
  };

  const coordinationFlows = [
    {
      id: 'workflow_execution',
      title: 'Workflow Execution Flow',
      icon: <Zap className="w-6 h-6" />,
      color: 'bg-blue-600',
      description: 'What actually happens on POST /api/supervisor/workflow/execute',
      steps: [
        { name: 'API Request', description: 'POST /api/supervisor/workflow/execute {contract_id}', icon: <FileText className="w-5 h-5" /> },
        { name: 'Enqueue Real Task', description: 'The exact same Celery task /api/intelligence/.../analyze uses - not a second pipeline', icon: <Bot className="w-5 h-5" /> },
        { name: 'PlanExecutionEngine Runs', description: 'Dependency-aware clause extraction, policy check, risk assessment, CUAD mitigation, redlines', icon: <Brain className="w-5 h-5" /> },
        { name: 'Quality Graded', description: 'A-F grade computed from real node_status + grounding + confidence', icon: <CheckCircle className="w-5 h-5" /> },
        { name: 'Status + Stream Available', description: 'GET .../status (Celery+Redis) and GET .../stream (live SSE progress)', icon: <Radio className="w-5 h-5" /> }
      ]
    },
    {
      id: 'recovery',
      title: 'Recovery Strategies',
      icon: <Shield className="w-6 h-6" />,
      color: 'bg-red-600',
      description: '3 real, working recovery mechanisms, plus the circuit breaker as a 4th - not a 4-way Retry/Switch/Degrade/Escalate system',
      steps: [
        { name: 'Retry', description: 'Each step retries up to 2x with exponential backoff before failing (fails fast on quota errors instead)', icon: <Zap className="w-5 h-5" /> },
        { name: 'Circuit Breaker', description: 'Real Redis-backed CLOSED/OPEN/HALF_OPEN state machine around every Gemini and Neo4j call - fails fast after repeated failures', icon: <Shield className="w-5 h-5" /> },
        { name: 'Degrade', description: 'CUAD mitigation already falls back Phase3 -> Phase2 -> Phase1 on failure; which tier ran is now surfaced as analysis_method instead of discarded', icon: <AlertTriangle className="w-5 h-5" /> },
        { name: 'Escalate', description: 'Any step that genuinely failed marks the result escalated:true and writes one WORKFLOW_ESCALATION audit event', icon: <Brain className="w-5 h-5" /> }
      ]
    },
    {
      id: 'quality_grading',
      title: 'Quality Grading (A-F)',
      icon: <CheckCircle className="w-6 h-6" />,
      color: 'bg-green-600',
      description: 'A deterministic rubric over signals PlanExecutionEngine already computes - not a decorative score',
      steps: [
        { name: 'Core Step Check', description: 'Any core step (extraction/policy check/risk) genuinely failed, or grounded_rate < 0.5 -> F', icon: <AlertTriangle className="w-5 h-5" /> },
        { name: 'Completion Check', description: 'processing_complete is False for another reason, or a partial step with low grounding -> D', icon: <Shield className="w-5 h-5" /> },
        { name: 'Partial Check', description: 'Any step only partially completed (e.g. some clauses failed policy evaluation) -> C', icon: <Bot className="w-5 h-5" /> },
        { name: 'Confidence Check', description: 'Everything succeeded, but grounding or confidence is middling -> B', icon: <CheckCircle className="w-5 h-5" /> },
        { name: 'Clean Result', description: 'Everything succeeded, grounded_rate >= 0.9, avg_confidence >= 0.85 -> A', icon: <CheckCircle className="w-5 h-5" /> }
      ]
    },
    {
      id: 'live_progress',
      title: 'Real-Time Progress Flow',
      icon: <Radio className="w-6 h-6" />,
      color: 'bg-purple-600',
      description: 'Genuine Redis pub/sub, not polling dressed up - the engine publishes without knowing if anyone is listening',
      steps: [
        { name: 'Engine Publishes', description: 'PlanExecutionEngine publishes one message per step transition to a Redis channel', icon: <Brain className="w-5 h-5" /> },
        { name: 'Client Subscribes', description: 'GET /api/supervisor/workflow/{contract_id}/stream (SSE) subscribes to that channel', icon: <Radio className="w-5 h-5" /> },
        { name: 'Live Updates', description: 'Each step\'s status streams as it actually happens, not a simulated sequence', icon: <Zap className="w-5 h-5" /> },
        { name: 'Terminates Honestly', description: 'Stream ends on a real workflow complete/failed message, or a 300s safety timeout', icon: <CheckCircle className="w-5 h-5" /> }
      ]
    }
  ];

  const coordinationBenefits = [
    {
      title: 'No Duplicate Pipeline',
      description: 'Enqueues the exact same PlanExecutionEngine-backed Celery task the regular analyze endpoint uses',
      icon: <Brain className="w-5 h-5 text-purple-600" />,
      color: 'border-purple-200 bg-purple-50'
    },
    {
      title: 'Real Circuit Breaker',
      description: 'Redis-backed state machine, not the removed per-request-fresh one that could never actually open',
      icon: <Shield className="w-5 h-5 text-red-600" />,
      color: 'border-red-200 bg-red-50'
    },
    {
      title: 'A-F Quality Grading',
      description: 'Deterministic rubric over real signals - node_status, grounding rate, extraction confidence',
      icon: <CheckCircle className="w-5 h-5 text-green-600" />,
      color: 'border-green-200 bg-green-50'
    },
    {
      title: 'Live Progress Streaming',
      description: 'Real Redis pub/sub + SSE - watch a running analysis step-by-step instead of polling blind',
      icon: <Radio className="w-5 h-5 text-blue-600" />,
      color: 'border-blue-200 bg-blue-50'
    },
    {
      title: 'Real Escalation Trail',
      description: 'A failed step writes a real audit event, queryable via GET /api/audit/trail/{contract_id}',
      icon: <AlertTriangle className="w-5 h-5 text-yellow-600" />,
      color: 'border-yellow-200 bg-yellow-50'
    },
    {
      title: 'Status Backed by Celery + Redis',
      description: 'Not the removed Supervisor\'s always-empty in-memory dict - the same AsyncResult mechanism as everywhere else',
      icon: <FileText className="w-5 h-5 text-indigo-600" />,
      color: 'border-indigo-200 bg-indigo-50'
    }
  ];

  return (
    <div className="space-y-8">
      <div className="text-center bg-white rounded-lg p-8 shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold text-slate-800 mb-3">Supervisor Agent</h1>
        <p className="text-lg text-slate-600 max-w-3xl mx-auto">
          A real, working POST /api/supervisor/workflow/execute built on top of PlanExecutionEngine,
          the real circuit breaker, the real audit trail, and a new Redis pub/sub progress channel -
          not the deleted dead-code Supervisor path this tab used to describe.
        </p>
      </div>

      <Card className="border-amber-200 bg-amber-50">
        <CardContent className="p-4 text-sm text-amber-900">
          <strong>What this replaced:</strong> an earlier Supervisor orchestration system (17 files -
          SupervisorAgent, a message bus, a quality scorer, a per-request-fresh circuit breaker that could
          never actually open) was removed as confirmed dead code - never called, its one distinct feature
          (quality grading) computed and never gated on anything. This page now describes the real rebuild
          that replaced it, piece by piece, below.
        </CardContent>
      </Card>

      {/* Coordination Flows */}
      <div className="space-y-6">
        <h2 className="text-2xl font-bold text-slate-800">How It Actually Works</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {coordinationFlows.map((flow) => (
            <Card
              key={flow.id}
              className={`cursor-pointer transition-all duration-200 hover:shadow-lg ${
                expandedFlows.has(flow.id) ? 'ring-2 ring-blue-500' : ''
              }`}
              onClick={() => toggleFlow(flow.id)}
            >
              <CardHeader className="pb-3">
                <div className="flex items-center gap-3">
                  <div className={`p-2 rounded-lg ${flow.color} text-white`}>
                    {flow.icon}
                  </div>
                  <div>
                    <CardTitle className="text-lg">{flow.title}</CardTitle>
                    <p className="text-sm text-slate-600">{flow.description}</p>
                  </div>
                </div>
              </CardHeader>

              {expandedFlows.has(flow.id) && (
                <CardContent className="border-t pt-4">
                  <div className="space-y-3">
                    {flow.steps.map((step, idx) => (
                      <div key={idx} className="flex items-center gap-3">
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          <div className="p-2 bg-slate-100 rounded-full">
                            {step.icon}
                          </div>
                          <div className="min-w-0 flex-1">
                            <h4 className="font-semibold text-sm text-slate-800">{step.name}</h4>
                            <p className="text-xs text-slate-600">{step.description}</p>
                          </div>
                        </div>
                        {idx < flow.steps.length - 1 && (
                          <ArrowRight className="w-4 h-4 text-slate-400 flex-shrink-0" />
                        )}
                      </div>
                    ))}
                  </div>

                  <div className="mt-4 pt-4 border-t">
                    <Badge variant="secondary" className="text-xs">
                      Click to collapse flow details
                    </Badge>
                  </div>
                </CardContent>
              )}
            </Card>
          ))}
        </div>
      </div>

      {/* Coordination Benefits */}
      <div className="space-y-6">
        <h2 className="text-2xl font-bold text-slate-800">What's Real Here</h2>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {coordinationBenefits.map((benefit, idx) => (
            <Card key={idx} className={`border ${benefit.color} hover:shadow-md transition-shadow`}>
              <CardContent className="p-4">
                <div className="flex items-start gap-3">
                  {benefit.icon}
                  <div>
                    <h3 className="font-semibold text-sm text-slate-800 mb-1">{benefit.title}</h3>
                    <p className="text-xs text-slate-600">{benefit.description}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Architecture Overview */}
      <Card className="border-slate-200">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-purple-600" />
            What Was Deliberately Not Built
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-slate-50 rounded-lg p-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div>
                <h4 className="font-medium mb-2 text-slate-700">Not built: "Switch" recovery</h4>
                <p className="text-xs text-slate-600">
                  Falling back to a different LLM provider when Gemini fails. This would be a real, separate,
                  larger decision (cost and quality trade-offs, needs another provider's key configured) -
                  deliberately left out rather than faked. Retry + Circuit Breaker + Degrade + Escalate is an
                  honest 4-mechanism recovery story without inventing a fourth strategy that isn't real.
                </p>
              </div>
              <div>
                <h4 className="font-medium mb-2 text-slate-700">Not built: a message bus</h4>
                <p className="text-xs text-slate-600">
                  A generic pub/sub abstraction with only one real publisher and one real subscriber isn't a
                  message bus, it's a disguised function call. The real Redis pub/sub channel above exists
                  because it solves an actual problem - live progress instead of blind polling - not to check
                  an architecture-diagram box.
                </p>
              </div>
            </div>
          </div>

          <div className="flex justify-center">
            <Badge variant="secondary" className="text-xs">
              Every flow above is wired to real, tested code - not a diagram of what might exist
            </Badge>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};
