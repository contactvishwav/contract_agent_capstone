import React from 'react';
import { CheckCircle, Clock, XCircle, ArrowRight } from 'lucide-react';

interface AgentExecution {
  agent_name: string;
  agent_role: string;
  status: 'processing' | 'completed' | 'error';
  input_summary: string;
  output_summary: string;
  processing_time_ms: number;
  error_message?: string;
}

interface WorkflowStatus {
  total_agents: number;
  completed_agents: number;
  failed_agents: number;
  agent_executions: AgentExecution[];
  data_flow: Array<{
    from_agent: string;
    to_agent: string;
    data_transferred: string;
  }>;
}

interface AgentWorkflowTrackerProps {
  workflowStatus: WorkflowStatus | null;
}

export const AgentWorkflowTracker: React.FC<AgentWorkflowTrackerProps> = ({
  workflowStatus
}) => {
  if (!workflowStatus || workflowStatus.agent_executions?.length === 0) return null;

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-5 h-5 text-green-600" />;
      case 'processing':
        return <Clock className="w-5 h-5 text-blue-600 animate-spin" />;
      case 'error':
        return <XCircle className="w-5 h-5 text-red-600" />;
      default:
        return <Clock className="w-5 h-5 text-gray-400" />;
    }
  };

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold text-blue-800">
          🤖 AI Agents Working
        </h3>
        <div className="text-sm text-gray-600">
          {workflowStatus.completed_agents}/{workflowStatus.total_agents} agents completed
        </div>
      </div>

      <div className="space-y-3">
        {workflowStatus.agent_executions.map((execution, index) => (
          <div key={index} className="flex items-start space-x-3 p-3 bg-gray-50 rounded-lg">
            <div className="flex-shrink-0 mt-1">
              {getStatusIcon(execution.status)}
            </div>
            
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-gray-900">
                  {execution.agent_name}
                </h4>
                {execution.processing_time_ms > 0 && (
                  <span className="text-xs text-gray-500">
                    {execution.processing_time_ms}ms
                  </span>
                )}
              </div>
              
              <p className="text-xs text-gray-600 mb-1">
                {execution.agent_role}
              </p>
              
              <div className="text-xs space-y-1">
                <div>
                  <span className="text-gray-500">Input:</span> {execution.input_summary}
                </div>
                {execution.output_summary && (
                  <div>
                    <span className="text-gray-500">Output:</span> {execution.output_summary}
                  </div>
                )}
                {execution.error_message && (
                  <div className="text-red-600">
                    <span className="text-gray-500">Error:</span> {execution.error_message}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {workflowStatus.data_flow.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-200">
          <h4 className="text-sm font-medium text-gray-700 mb-2">Data Flow</h4>
          <div className="space-y-2">
            {workflowStatus.data_flow.map((flow, index) => (
              <div key={index} className="flex items-center text-xs text-gray-600">
                <span className="font-medium">{flow.from_agent}</span>
                <ArrowRight className="w-3 h-3 mx-2" />
                <span className="font-medium">{flow.to_agent}</span>
                <span className="ml-2 text-gray-500">({flow.data_transferred})</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};