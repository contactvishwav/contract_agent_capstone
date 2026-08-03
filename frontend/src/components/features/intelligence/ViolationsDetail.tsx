import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { Button } from '../../shared/ui/button';
import { AlertTriangle, CheckCircle, XCircle, Shield, TrendingDown, Clock, Download, Search, Calendar } from 'lucide-react';

interface PolicyViolation {
  clause_type: string;
  issue: string;
  severity: string;
  suggested_fix: string;
  clause_content: string;
}

interface ViolationsDetailProps {
  violations: PolicyViolation[];
  contractId?: string;
}

export const ViolationsDetail: React.FC<ViolationsDetailProps> = ({ violations, contractId }) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [severityFilter, setSeverityFilter] = useState<string>('ALL');
  const [sortBy, setSortBy] = useState<'severity' | 'type' | 'policy'>('severity');

  const getSeverityIcon = (severity: string) => {
    switch (severity.toUpperCase()) {
      case 'CRITICAL': return <XCircle className="h-4 w-4 text-red-500" />;
      case 'HIGH': return <AlertTriangle className="h-4 w-4 text-orange-500" />;
      case 'MEDIUM': return <AlertTriangle className="h-4 w-4 text-yellow-500" />;
      case 'LOW': return <CheckCircle className="h-4 w-4 text-green-500" />;
      default: return <AlertTriangle className="h-4 w-4 text-gray-500" />;
    }
  };

  const getSeverityColor = (severity: string) => {
    switch (severity.toUpperCase()) {
      case 'CRITICAL': return 'bg-red-100 border-red-200';
      case 'HIGH': return 'bg-orange-100 border-orange-200';
      case 'MEDIUM': return 'bg-yellow-100 border-yellow-200';
      case 'LOW': return 'bg-green-100 border-green-200';
      default: return 'bg-gray-100 border-gray-200';
    }
  };

  const getSeverityWeight = (severity: string) => {
    switch (severity.toUpperCase()) {
      case 'CRITICAL': return 4;
      case 'HIGH': return 3;
      case 'MEDIUM': return 2;
      case 'LOW': return 1;
      default: return 0;
    }
  };

  const getInternalPolicy = (clauseType: string) => {
    const policies: Record<string, { policy: string; section: string; requirement: string }> = {
      'Termination': {
        policy: 'Contract Termination Policy 2.1',
        section: 'Section 4.2 - Notice Requirements',
        requirement: 'Minimum 30-day written notice required for termination'
      },
      'Liability': {
        policy: 'Risk Management Policy 1.5',
        section: 'Section 3.1 - Liability Caps',
        requirement: 'Liability must be capped at contract value or $1M, whichever is lower'
      },
      'Indemnification': {
        policy: 'Legal Risk Policy 3.2',
        section: 'Section 2.4 - Indemnification Limits',
        requirement: 'Mutual indemnification required; no unlimited indemnity'
      },
      'Payment Terms': {
        policy: 'Financial Controls Policy 4.1',
        section: 'Section 1.3 - Payment Standards',
        requirement: 'Net 30 payment terms maximum; no advance payments over $10K'
      },
      'Intellectual Property': {
        policy: 'IP Protection Policy 5.1',
        section: 'Section 2.1 - IP ownership',
        requirement: 'Company retains all IP rights to work product and derivatives'
      },
      'Confidentiality': {
        policy: 'Data Protection Policy 6.2',
        section: 'Section 3.2 - Confidentiality Standards',
        requirement: 'Mutual confidentiality with 5-year term minimum'
      },
      'Governing Law': {
        policy: 'Legal Jurisdiction Policy 7.1',
        section: 'Section 1.1 - Jurisdiction Requirements',
        requirement: 'Contracts must be governed by Delaware or company headquarters state law'
      }
    };
    
    return policies[clauseType] || {
      policy: 'General Contract Policy 1.0',
      section: 'Section 1.1 - Standard Requirements',
      requirement: 'Must comply with company contracting standards'
    };
  };

  const getBusinessImpact = (severity: string, _clauseType: string) => {
    const impacts: Record<string, { financial: string; legal: string; operational: string; timeline: string }> = {
      'CRITICAL': {
        financial: 'Potential financial loss exceeding $100K',
        legal: 'High litigation risk and regulatory exposure',
        operational: 'Severe operational disruption possible',
        timeline: 'Immediate resolution required'
      },
      'HIGH': {
        financial: 'Potential financial loss $25K-$100K',
        legal: 'Moderate litigation risk',
        operational: 'Significant operational impact',
        timeline: 'Resolution required within 1 week'
      },
      'MEDIUM': {
        financial: 'Potential financial loss $5K-$25K',
        legal: 'Limited legal exposure',
        operational: 'Minor operational impact',
        timeline: 'Resolution required within 2 weeks'
      },
      'LOW': {
        financial: 'Minimal financial impact under $5K',
        legal: 'Low legal risk',
        operational: 'Negligible operational impact',
        timeline: 'Resolution within 30 days acceptable'
      }
    };

    return impacts[severity.toUpperCase()] || impacts['MEDIUM'];
  };

  const getComplianceActions = (severity: string) => {
    const actions: Record<string, string[]> = {
      'CRITICAL': [
        'Escalate to Legal Department immediately',
        'Halt contract execution until resolved',
        'Engage external counsel if needed',
        'Document all remediation efforts'
      ],
      'HIGH': [
        'Notify Legal Department within 24 hours',
        'Negotiate amendment before signing',
        'Obtain management approval for any exceptions',
        'Implement additional monitoring controls'
      ],
      'MEDIUM': [
        'Review with Legal Department',
        'Consider contract amendment',
        'Document risk acceptance if proceeding',
        'Establish periodic review schedule'
      ],
      'LOW': [
        'Note in contract file',
        'Consider for future negotiations',
        'Monitor during contract performance',
        'Include in annual policy review'
      ]
    };

    return actions[severity.toUpperCase()] || actions['MEDIUM'];
  };

  const exportReport = () => {
    const reportData = {
      contractId: contractId || 'Unknown',
      analysisDate: new Date().toISOString(),
      totalViolations: violations.length,
      violationsSummary: getViolationsSummary(),
      violations: filteredAndSortedViolations.map(v => ({
        ...v,
        policy: getInternalPolicy(v.clause_type),
        impact: getBusinessImpact(v.severity, v.clause_type),
        actions: getComplianceActions(v.severity)
      }))
    };
    
    const blob = new Blob([JSON.stringify(reportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `violations-report-${contractId || 'contract'}-${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const getViolationsSummary = () => {
    const summary = violations.reduce((acc, v) => {
      acc[v.severity] = (acc[v.severity] || 0) + 1;
      return acc;
    }, {} as Record<string, number>);
    
    return summary;
  };

  // Filter and sort violations
  const filteredAndSortedViolations = violations
    .filter(v => {
      const matchesSearch = searchTerm === '' || 
        v.clause_type.toLowerCase().includes(searchTerm.toLowerCase()) ||
        v.issue.toLowerCase().includes(searchTerm.toLowerCase());
      const matchesSeverity = severityFilter === 'ALL' || v.severity === severityFilter;
      return matchesSearch && matchesSeverity;
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'severity':
          return getSeverityWeight(b.severity) - getSeverityWeight(a.severity);
        case 'type':
          return a.clause_type.localeCompare(b.clause_type);
        case 'policy':
          return getInternalPolicy(a.clause_type).policy.localeCompare(getInternalPolicy(b.clause_type).policy);
        default:
          return 0;
      }
    });

  const summary = getViolationsSummary();

  return (
    <div className="space-y-6">
      {/* Header with Contract Info and Export */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <Calendar className="h-4 w-4" />
            <span>Analysis: {new Date().toLocaleDateString()}</span>
          </div>
          {contractId && (
            <div className="text-sm text-slate-600">
              Contract: <span className="font-medium">{contractId}</span>
            </div>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={exportReport}>
          <Download className="h-4 w-4 mr-2" />
          Export Report
        </Button>
      </div>

      {/* Summary Statistics */}
      <Card className="border-slate-200 bg-slate-50">
        <CardHeader>
          <CardTitle className="text-lg">Violations Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-slate-800">{violations.length}</div>
              <div className="text-sm text-slate-600">Total Violations</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-red-600">{summary.CRITICAL || 0}</div>
              <div className="text-sm text-slate-600">Critical</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-orange-600">{summary.HIGH || 0}</div>
              <div className="text-sm text-slate-600">High</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-yellow-600">{summary.MEDIUM || 0}</div>
              <div className="text-sm text-slate-600">Medium</div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Search and Filter Controls */}
      <Card className="border-slate-200">
        <CardContent className="pt-6">
          <div className="flex flex-col md:flex-row gap-4">
            <div className="flex-1 relative">
              <Search className="h-4 w-4 absolute left-3 top-3 text-slate-400" />
              <input
                type="text"
                placeholder="Search violations..."
                className="w-full pl-10 pr-4 py-2 border border-slate-300 rounded-md text-sm"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <select
                className="px-3 py-2 border border-slate-300 rounded-md text-sm"
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
              >
                <option value="ALL">All Severities</option>
                <option value="CRITICAL">Critical</option>
                <option value="HIGH">High</option>
                <option value="MEDIUM">Medium</option>
                <option value="LOW">Low</option>
              </select>
              <select
                className="px-3 py-2 border border-slate-300 rounded-md text-sm"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as 'severity' | 'type' | 'policy')}
              >
                <option value="severity">Sort by Severity</option>
                <option value="type">Sort by Type</option>
                <option value="policy">Sort by Policy</option>
              </select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Violations List */}
      <div className="text-sm text-slate-600 mb-4">
        Showing {filteredAndSortedViolations.length} of {violations.length} violations
      </div>
      
      {filteredAndSortedViolations.map((violation, index) => {
        const policy = getInternalPolicy(violation.clause_type);
        const impact = getBusinessImpact(violation.severity, violation.clause_type);
        const actions = getComplianceActions(violation.severity);

        return (
          <Card key={index} className={`border ${getSeverityColor(violation.severity)}`}>
            <CardHeader className="pb-3">
              <div className="flex items-center gap-2">
                {getSeverityIcon(violation.severity)}
                <CardTitle className="text-lg text-slate-800">{violation.clause_type}</CardTitle>
                <Badge variant="outline" className="ml-auto">
                  {violation.severity}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Policy Violation Details */}
              <div>
                <h4 className="font-semibold text-red-700 mb-2 flex items-center gap-2">
                  <XCircle className="h-4 w-4" />
                  Policy Violation Identified
                </h4>
                <p className="text-sm text-slate-700 bg-red-50 p-3 rounded border border-red-200">
                  {violation.issue}
                </p>
              </div>

              {/* Internal Policy Reference */}
              <div className="bg-blue-50 p-4 rounded border border-blue-200">
                <h4 className="font-semibold text-blue-700 mb-3 flex items-center gap-2">
                  <Shield className="h-4 w-4" />
                  Internal Policy Reference
                </h4>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="font-medium text-slate-700">Policy:</span>
                    <span className="text-blue-700">{policy.policy}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="font-medium text-slate-700">Section:</span>
                    <span className="text-blue-700">{policy.section}</span>
                  </div>
                  <div className="mt-3">
                    <span className="font-medium text-slate-700">Requirement:</span>
                    <p className="text-slate-600 mt-1 italic">"{policy.requirement}"</p>
                  </div>
                </div>
              </div>

              {/* Business Impact Analysis */}
              <div className="bg-orange-50 p-4 rounded border border-orange-200">
                <h4 className="font-semibold text-orange-700 mb-3 flex items-center gap-2">
                  <TrendingDown className="h-4 w-4" />
                  Business Impact if Not Resolved
                </h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="font-medium text-slate-700">Financial Risk:</span>
                    <p className="text-orange-800 mt-1">{impact.financial}</p>
                  </div>
                  <div>
                    <span className="font-medium text-slate-700">Legal Risk:</span>
                    <p className="text-orange-800 mt-1">{impact.legal}</p>
                  </div>
                  <div>
                    <span className="font-medium text-slate-700">Operational Risk:</span>
                    <p className="text-orange-800 mt-1">{impact.operational}</p>
                  </div>
                  <div>
                    <span className="font-medium text-slate-700 flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      Timeline:
                    </span>
                    <p className="text-orange-800 mt-1">{impact.timeline}</p>
                  </div>
                </div>
              </div>

              {/* Recommended Solution */}
              <div>
                <h4 className="font-semibold text-green-700 mb-2 flex items-center gap-2">
                  <CheckCircle className="h-4 w-4" />
                  Recommended Solution
                </h4>
                <p className="text-sm text-slate-700 bg-green-50 p-3 rounded border border-green-200">
                  {violation.suggested_fix}
                </p>
              </div>

              {/* Compliance Actions Required */}
              <div className="bg-slate-50 p-4 rounded border border-slate-200">
                <h4 className="font-semibold text-slate-700 mb-3">Compliance Actions Required</h4>
                <ul className="space-y-2">
                  {actions.map((action, actionIndex) => (
                    <li key={actionIndex} className="flex items-start gap-2 text-sm">
                      <div className="w-1.5 h-1.5 bg-slate-400 rounded-full mt-2 flex-shrink-0" />
                      <span className="text-slate-700">{action}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Original Contract Language */}
              {violation.clause_content && (
                <div>
                  <h4 className="font-semibold text-slate-700 mb-2">Original Contract Language</h4>
                  <div className="bg-slate-50 p-3 rounded border border-slate-200">
                    <p className="text-sm text-slate-600 italic leading-relaxed whitespace-pre-wrap">
                      "{violation.clause_content}"
                    </p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}

      {filteredAndSortedViolations.length === 0 && (
        <div className="text-center py-8 text-slate-500">
          No violations match the current search and filter criteria.
        </div>
      )}
    </div>
  );
};