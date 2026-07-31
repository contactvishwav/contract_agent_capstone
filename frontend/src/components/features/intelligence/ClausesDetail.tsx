import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { Shield, AlertTriangle, TrendingDown, CheckCircle, Clock, Target, FileCheck, HelpCircle, History } from 'lucide-react';

interface ContractClause {
  clause_type: string;
  content: string;
  risk_level: string;
  confidence_score: number;
  location: string;
  clause_id?: string;
  grounded?: boolean;
  original_risk_level?: string | null;
  learned_risk_adjustment?: string | null;
  pattern_confidence?: number | null;
  risk_adjustment_pattern_id?: string | null;
}

interface ClausesDetailProps {
  clauses: ContractClause[];
}

export const ClausesDetail: React.FC<ClausesDetailProps> = ({ clauses }) => {
  const getRiskColor = (level: string) => {
    switch (level.toUpperCase()) {
      case 'CRITICAL': return 'bg-red-500 text-white';
      case 'HIGH': return 'bg-orange-500 text-white';
      case 'MEDIUM': return 'bg-yellow-500 text-white';
      case 'LOW': return 'bg-green-500 text-white';
      default: return 'bg-gray-500 text-white';
    }
  };

  const getRiskIcon = (level: string) => {
    switch (level.toUpperCase()) {
      case 'CRITICAL': return <AlertTriangle className="h-4 w-4 text-red-500" />;
      case 'HIGH': return <AlertTriangle className="h-4 w-4 text-orange-500" />;
      case 'MEDIUM': return <AlertTriangle className="h-4 w-4 text-yellow-500" />;
      case 'LOW': return <CheckCircle className="h-4 w-4 text-green-500" />;
      default: return <Shield className="h-4 w-4 text-gray-500" />;
    }
  };

  const getPolicyAlignment = (clauseType: string, riskLevel: string) => {
    const alignments = {
      'Termination': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'Contract Termination Policy 2.1',
        requirement: 'Balanced termination rights with adequate notice periods',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'Termination clause may favor counterparty or lack proper notice requirements' : null
      },
      'Liability': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'Risk Management Policy 1.5',
        requirement: 'Mutual liability caps not exceeding contract value',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'Liability exposure may exceed acceptable risk thresholds' : null
      },
      'Indemnification': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'Legal Risk Policy 3.2',
        requirement: 'Mutual indemnification with reasonable scope limitations',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'Indemnification terms may create unlimited or one-sided exposure' : null
      },
      'Payment Terms': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'Financial Controls Policy 4.1',
        requirement: 'Standard payment terms aligned with cash flow policies',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'Payment terms may impact cash flow or create financial risk' : null
      },
      'Intellectual Property': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'IP Protection Policy 5.1',
        requirement: 'Clear IP ownership and protection of company assets',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'IP rights may not adequately protect company interests' : null
      },
      'Confidentiality': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'Data Protection Policy 6.2',
        requirement: 'Adequate protection of confidential information',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'Confidentiality protections may be insufficient' : null
      },
      'Governing Law': {
        compliant: riskLevel.toUpperCase() === 'LOW',
        policy: 'Legal Jurisdiction Policy 7.1',
        requirement: 'Favorable jurisdiction and governing law selection',
        concern: riskLevel.toUpperCase() !== 'LOW' ? 'Jurisdiction or governing law may create legal disadvantages' : null
      }
    };

    return alignments[clauseType] || {
      compliant: riskLevel.toUpperCase() === 'LOW',
      policy: 'General Contract Policy 1.0',
      requirement: 'Standard commercial terms and conditions',
      concern: riskLevel.toUpperCase() !== 'LOW' ? 'Clause may not align with company standards' : null
    };
  };

  const getRiskImpact = (riskLevel: string, clauseType: string) => {
    const impacts = {
      'CRITICAL': {
        business: 'Severe business disruption and financial loss likely',
        legal: 'High litigation risk and potential regulatory violations',
        operational: 'Major operational constraints and compliance issues',
        financial: 'Potential losses exceeding $100K',
        timeline: 'Immediate attention required before contract execution'
      },
      'HIGH': {
        business: 'Significant business impact and competitive disadvantage',
        legal: 'Moderate litigation risk and legal exposure',
        operational: 'Notable operational limitations and process impacts',
        financial: 'Potential losses between $25K-$100K',
        timeline: 'Resolution needed within 1 week'
      },
      'MEDIUM': {
        business: 'Moderate business impact with manageable consequences',
        legal: 'Limited legal exposure with standard commercial risk',
        operational: 'Minor operational adjustments may be required',
        financial: 'Potential losses between $5K-$25K',
        timeline: 'Address within 2 weeks'
      },
      'LOW': {
        business: 'Minimal business impact within acceptable parameters',
        legal: 'Standard legal risk typical for commercial contracts',
        operational: 'No significant operational impact expected',
        financial: 'Minimal financial exposure under $5K',
        timeline: 'Monitor during contract performance'
      }
    };

    return impacts[riskLevel.toUpperCase()] || impacts['MEDIUM'];
  };

  const getRecommendedActions = (riskLevel: string, clauseType: string) => {
    const actions = {
      'CRITICAL': [
        'Engage legal counsel immediately for clause revision',
        'Do not execute contract until clause is renegotiated',
        'Escalate to senior management for approval',
        'Consider alternative contract structures'
      ],
      'HIGH': [
        'Negotiate clause modification with counterparty',
        'Obtain legal department approval before proceeding',
        'Document risk acceptance rationale if unchanged',
        'Implement additional monitoring and controls'
      ],
      'MEDIUM': [
        'Review clause with legal team for potential improvements',
        'Consider negotiating better terms in future amendments',
        'Establish clear performance monitoring procedures',
        'Document any risk mitigation strategies'
      ],
      'LOW': [
        'Standard clause acceptable for execution',
        'Monitor performance during contract term',
        'Note any lessons learned for future negotiations',
        'Include in routine contract compliance reviews'
      ]
    };

    return actions[riskLevel.toUpperCase()] || actions['MEDIUM'];
  };

  return (
    <div className="space-y-4">
      <div className="text-sm text-slate-600 mb-4">
        Found {clauses.length} key clauses in the contract document
      </div>

      {clauses.map((clause, index) => {
        const policyAlignment = getPolicyAlignment(clause.clause_type, clause.risk_level);
        const riskImpact = getRiskImpact(clause.risk_level, clause.clause_type);
        const recommendedActions = getRecommendedActions(clause.risk_level, clause.clause_type);
        const hasLearnedAdjustment = Boolean(clause.learned_risk_adjustment);

        return (
          <Card key={index} className="border-slate-200">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {getRiskIcon(clause.risk_level)}
                  <CardTitle className="text-lg text-slate-800">{clause.clause_type}</CardTitle>
                </div>
                <div className="flex items-center gap-2">
                  <Badge className={getRiskColor(clause.risk_level)}>
                    {clause.risk_level}
                  </Badge>
                  {clause.grounded === false ? (
                    <Badge variant="outline" className="border-amber-300 text-amber-700 bg-amber-50">
                      <HelpCircle className="h-3 w-3 mr-1 inline align-text-bottom" />
                      Unverified
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="border-slate-300 text-slate-600">
                      <FileCheck className="h-3 w-3 mr-1 inline align-text-bottom" />
                      Verified
                    </Badge>
                  )}
                  <span className="text-xs text-slate-500">
                    {(clause.confidence_score * 100).toFixed(1)}% confidence
                  </span>
                </div>
              </div>
              {clause.location && (
                <p className="text-sm text-slate-500">{clause.location}</p>
              )}
              {clause.clause_id && (
                <p className="text-xs text-slate-400 font-mono mt-1">ID: {clause.clause_id}</p>
              )}
              {hasLearnedAdjustment && (
                <div className="mt-3 flex items-start gap-2 text-xs text-indigo-800 bg-indigo-50 border border-indigo-200 rounded px-3 py-2">
                  <History className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-indigo-500" />
                  <span>
                    Risk adjusted from <strong>{clause.original_risk_level}</strong> to{' '}
                    <strong>{clause.learned_risk_adjustment}</strong> based on historical review patterns
                    {typeof clause.pattern_confidence === 'number' && (
                      <> ({(clause.pattern_confidence * 100).toFixed(0)}% confidence)</>
                    )}
                    .
                  </span>
                </div>
              )}
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Original Contract Language */}
              <div>
                <h4 className="font-semibold text-slate-700 mb-2">Contract Language</h4>
                <div className="bg-slate-50 p-4 rounded-lg border border-slate-200">
                  <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                    {clause.content}
                  </p>
                </div>
              </div>

              {/* Policy Alignment Analysis */}
              <div className={`p-4 rounded border ${policyAlignment.compliant ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                <h4 className={`font-semibold mb-3 flex items-center gap-2 ${policyAlignment.compliant ? 'text-green-700' : 'text-red-700'}`}>
                  <Shield className="h-4 w-4" />
                  Internal Policy Alignment
                </h4>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="font-medium text-slate-700">Policy:</span>
                    <span className={policyAlignment.compliant ? 'text-green-700' : 'text-red-700'}>
                      {policyAlignment.policy}
                    </span>
                  </div>
                  <div className="mt-3">
                    <span className="font-medium text-slate-700">Requirement:</span>
                    <p className="text-slate-600 mt-1 italic">"{policyAlignment.requirement}"</p>
                  </div>
                  {policyAlignment.concern && (
                    <div className="mt-3 p-3 bg-red-100 rounded border border-red-200">
                      <span className="font-medium text-red-700">Policy Concern:</span>
                      <p className="text-red-800 mt-1">{policyAlignment.concern}</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Risk Impact Analysis */}
              {clause.risk_level.toUpperCase() !== 'LOW' && (
                <div className="bg-orange-50 p-4 rounded border border-orange-200">
                  <h4 className="font-semibold text-orange-700 mb-3 flex items-center gap-2">
                    <TrendingDown className="h-4 w-4" />
                    Risk Impact if Not Addressed
                  </h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="font-medium text-slate-700">Business Impact:</span>
                      <p className="text-orange-800 mt-1">{riskImpact.business}</p>
                    </div>
                    <div>
                      <span className="font-medium text-slate-700">Legal Risk:</span>
                      <p className="text-orange-800 mt-1">{riskImpact.legal}</p>
                    </div>
                    <div>
                      <span className="font-medium text-slate-700">Operational Impact:</span>
                      <p className="text-orange-800 mt-1">{riskImpact.operational}</p>
                    </div>
                    <div>
                      <span className="font-medium text-slate-700">Financial Risk:</span>
                      <p className="text-orange-800 mt-1">{riskImpact.financial}</p>
                    </div>
                  </div>
                  <div className="mt-3 pt-3 border-t border-orange-200">
                    <span className="font-medium text-slate-700 flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      Action Timeline:
                    </span>
                    <p className="text-orange-800 mt-1 font-medium">{riskImpact.timeline}</p>
                  </div>
                </div>
              )}

              {/* Recommended Actions */}
              <div className="bg-blue-50 p-4 rounded border border-blue-200">
                <h4 className="font-semibold text-blue-700 mb-3 flex items-center gap-2">
                  <Target className="h-4 w-4" />
                  Recommended Actions
                </h4>
                <ul className="space-y-2">
                  {recommendedActions.map((action, actionIndex) => (
                    <li key={actionIndex} className="flex items-start gap-2 text-sm">
                      <div className="w-1.5 h-1.5 bg-blue-500 rounded-full mt-2 flex-shrink-0" />
                      <span className="text-blue-800">{action}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
};
