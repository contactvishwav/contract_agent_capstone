import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { Shield, AlertTriangle, CheckCircle, XCircle, Calculator, Target, TrendingUp, Download, Calendar } from 'lucide-react';
import { Button } from '../../shared/ui/button';

interface ScoreBreakdownEntry {
  factor: string;
  points: number;
}

interface RiskAssessment {
  overall_risk_score: number;
  risk_level: string;
  critical_issues: string[];
  recommendations: string[];
  // Real, itemized contributions from the backend's deterministic scoring
  // formula (RiskCalculatorTool) - absent on analyses persisted before
  // this field existed, in which case the UI falls back to the older
  // heuristic breakdown below.
  score_breakdown?: ScoreBreakdownEntry[];
}

interface RiskDetailProps {
  riskAssessment: RiskAssessment;
  contractId?: string;
}

export const RiskDetail: React.FC<RiskDetailProps> = ({ riskAssessment, contractId }) => {
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
      case 'CRITICAL': return <XCircle className="h-6 w-6 text-red-500" />;
      case 'HIGH': return <AlertTriangle className="h-6 w-6 text-orange-500" />;
      case 'MEDIUM': return <AlertTriangle className="h-6 w-6 text-yellow-500" />;
      case 'LOW': return <CheckCircle className="h-6 w-6 text-green-500" />;
      default: return <Shield className="h-6 w-6 text-gray-500" />;
    }
  };

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-red-600';
    if (score >= 60) return 'text-orange-600';
    if (score >= 40) return 'text-yellow-600';
    return 'text-green-600';
  };

  // Dynamic risk factors based on actual assessment data
  const getRiskFactors = (assessment: RiskAssessment) => {
    const factors = [];
    const score = assessment.overall_risk_score;
    const criticalCount = assessment.critical_issues?.length || 0;
    
    // Critical issues factor
    if (criticalCount > 0) {
      factors.push({ 
        factor: `Critical Issues (${criticalCount} found)`, 
        impact: criticalCount >= 3 ? 'High' : 'Medium', 
        weight: '35%' 
      });
    }
    
    // Policy violations (inferred from score)
    if (score >= 60) {
      factors.push({ 
        factor: 'Policy Violations Detected', 
        impact: score >= 80 ? 'High' : 'Medium', 
        weight: '25%' 
      });
    }
    
    // Contract complexity
    factors.push({ 
      factor: 'Contract Complexity', 
      impact: score >= 70 ? 'High' : score >= 40 ? 'Medium' : 'Low', 
      weight: '20%' 
    });
    
    // Compliance assessment
    factors.push({ 
      factor: 'Compliance Assessment', 
      impact: score >= 60 ? 'Medium' : 'Low', 
      weight: '20%' 
    });
    
    return factors;
  };

  const getActionableRecommendations = (assessment: RiskAssessment) => {
    const actionable = [];
    const score = assessment.overall_risk_score;
    const criticalCount = assessment.critical_issues?.length || 0;
    
    // Handle edge case: zero or very low scores
    if (score === 0) {
      actionable.push({
        priority: 'LOW',
        action: 'Verify Analysis Results',
        description: 'Risk score of 0 may indicate incomplete analysis. Review contract manually.',
        timeline: '1 day'
      });
      return actionable;
    }
    
    if (score >= 80 || criticalCount >= 3) {
      actionable.push({
        priority: 'IMMEDIATE',
        action: 'Legal Review Required',
        description: 'Engage legal counsel before signing this contract',
        timeline: 'Before execution'
      });
      actionable.push({
        priority: 'HIGH',
        action: 'Renegotiate Critical Terms',
        description: 'Address high-risk clauses identified in the analysis',
        timeline: '1-2 weeks'
      });
    }
    
    if (criticalCount > 0) {
      actionable.push({
        priority: 'HIGH',
        action: 'Address Critical Issues',
        description: `Resolve ${criticalCount} critical issue${criticalCount > 1 ? 's' : ''} before proceeding`,
        timeline: '1 week'
      });
    }
    
    if (score >= 40) {
      actionable.push({
        priority: 'MEDIUM',
        action: 'Risk Mitigation Plan',
        description: 'Develop strategies to minimize identified risks',
        timeline: '2-3 weeks'
      });
      actionable.push({
        priority: 'MEDIUM',
        action: 'Compliance Verification',
        description: 'Ensure all terms comply with company policies',
        timeline: '1 week'
      });
    }
    
    actionable.push({
      priority: 'LOW',
      action: 'Documentation & Monitoring',
      description: 'Document risk assessment and establish monitoring procedures',
      timeline: 'Ongoing'
    });
    
    return actionable;
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'IMMEDIATE': return 'bg-red-100 text-red-800 border-red-200';
      case 'HIGH': return 'bg-orange-100 text-orange-800 border-orange-200';
      case 'MEDIUM': return 'bg-yellow-100 text-yellow-800 border-yellow-200';
      case 'LOW': return 'bg-blue-100 text-blue-800 border-blue-200';
      default: return 'bg-gray-100 text-gray-800 border-gray-200';
    }
  };

  const exportReport = () => {
    const reportData = {
      contractId: contractId || 'Unknown',
      analysisDate: new Date().toISOString(),
      riskScore: riskAssessment.overall_risk_score,
      riskLevel: riskAssessment.risk_level,
      criticalIssues: riskAssessment.critical_issues,
      recommendations: riskAssessment.recommendations,
      actionableItems: getActionableRecommendations(riskAssessment)
    };
    
    const blob = new Blob([JSON.stringify(reportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `risk-assessment-${contractId || 'report'}-${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Handle missing or invalid data
  if (!riskAssessment || typeof riskAssessment.overall_risk_score !== 'number') {
    return (
      <div className="text-center py-8">
        <Shield className="h-12 w-12 text-slate-400 mx-auto mb-4" />
        <p className="text-slate-600">Risk assessment data is incomplete or invalid.</p>
      </div>
    );
  }

  const hasRealBreakdown = Array.isArray(riskAssessment.score_breakdown) && riskAssessment.score_breakdown.length > 0;
  const riskFactors = hasRealBreakdown ? riskAssessment.score_breakdown! : getRiskFactors(riskAssessment);
  const actionableRecommendations = getActionableRecommendations(riskAssessment);
  const analysisDate = new Date().toLocaleDateString();

  return (
    <div className="space-y-6">
      {/* Header with Export */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2 text-sm text-slate-600">
          <Calendar className="h-4 w-4" />
          <span>Analysis Date: {analysisDate}</span>
        </div>
        <Button variant="outline" size="sm" onClick={exportReport}>
          <Download className="h-4 w-4 mr-2" />
          Export Report
        </Button>
      </div>

      {/* Overall Risk Summary */}
      <Card className="border-slate-200">
        <CardHeader>
          <CardTitle className="flex items-center gap-3">
            {getRiskIcon(riskAssessment.risk_level)}
            <span>Overall Risk Assessment</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between mb-4">
            <div>
              <div 
                className={`text-3xl font-bold ${getScoreColor(riskAssessment.overall_risk_score)}`}
                aria-label={`Risk score: ${riskAssessment.overall_risk_score} out of 100`}
              >
                {riskAssessment.overall_risk_score}/100
              </div>
              <p className="text-sm text-slate-600">Risk Score</p>
            </div>
            <Badge className={getRiskColor(riskAssessment.risk_level)}>
              {riskAssessment.risk_level} RISK
            </Badge>
          </div>
          
          <div className="w-full bg-slate-200 rounded-full h-3 mb-4" role="progressbar" aria-valuenow={riskAssessment.overall_risk_score} aria-valuemin={0} aria-valuemax={100}>
            <div 
              className={`h-3 rounded-full transition-all duration-300 ${
                riskAssessment.overall_risk_score >= 80 ? 'bg-red-500' :
                riskAssessment.overall_risk_score >= 60 ? 'bg-orange-500' :
                riskAssessment.overall_risk_score >= 40 ? 'bg-yellow-500' : 'bg-green-500'
              }`}
              style={{ width: `${Math.max(riskAssessment.overall_risk_score, 2)}%` }}
            />
          </div>

          <div className="text-sm text-slate-600">
            <p className="mb-2">
              <strong>Risk Level Interpretation:</strong>
            </p>
            <ul className="text-xs space-y-1 ml-4">
              <li>• 0-39: Low Risk - Standard contract terms</li>
              <li>• 40-59: Medium Risk - Some concerns, manageable</li>
              <li>• 60-79: High Risk - Significant issues, review needed</li>
              <li>• 80-100: Critical Risk - Legal review required</li>
            </ul>
          </div>
        </CardContent>
      </Card>

      {/* Score Calculation Breakdown */}
      <Card className="border-blue-200 bg-blue-50">
        <CardHeader>
          <CardTitle className="text-blue-700 flex items-center gap-2">
            <Calculator className="h-5 w-5" />
            Risk Score Calculation
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-blue-800 mb-4">
            {hasRealBreakdown
              ? 'The risk score is calculated deterministically: a base score for the contract type, plus a fixed point value for each policy violation found below. Reproducible and auditable, not a black-box AI judgment.'
              : 'The risk score is calculated from multiple contract factors:'}
          </p>
          <div className="space-y-3">
            {riskFactors.map((factor, index) => (
              <div key={index} className="flex items-center justify-between bg-white p-3 rounded border border-blue-200">
                <div className="flex-1">
                  <p className="text-sm font-medium text-slate-800">{factor.factor}</p>
                  {!hasRealBreakdown && 'impact' in factor && (
                    <p className="text-xs text-slate-600">Impact: {(factor as { impact: string }).impact}</p>
                  )}
                </div>
                <div className="text-right">
                  <p className="text-sm font-medium text-blue-700">
                    {hasRealBreakdown ? `${(factor as ScoreBreakdownEntry).points > 0 ? '+' : ''}${(factor as ScoreBreakdownEntry).points}` : (factor as { weight: string }).weight}
                  </p>
                  <p className="text-xs text-slate-500">{hasRealBreakdown ? 'Points' : 'Weight'}</p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Actionable Recommendations */}
      <Card className="border-green-200 bg-green-50">
        <CardHeader>
          <CardTitle className="text-green-700 flex items-center gap-2">
            <Target className="h-5 w-5" />
            Actionable Recommendations
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-green-800 mb-4">
            Prioritized action items to address identified risks:
          </p>
          <div className="space-y-4">
            {actionableRecommendations.map((rec, index) => (
              <div key={index} className="bg-white p-4 rounded border border-green-200">
                <div className="flex items-start justify-between mb-2">
                  <h4 className="font-semibold text-slate-800">{rec.action}</h4>
                  <Badge className={`text-xs ${getPriorityColor(rec.priority)}`}>
                    {rec.priority}
                  </Badge>
                </div>
                <p className="text-sm text-slate-700 mb-2">{rec.description}</p>
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <TrendingUp className="h-3 w-3" />
                  <span>Timeline: {rec.timeline}</span>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Critical Issues */}
      {riskAssessment.critical_issues && riskAssessment.critical_issues.length > 0 && (
        <Card className="border-red-200 bg-red-50">
          <CardHeader>
            <CardTitle className="text-red-700 flex items-center gap-2">
              <XCircle className="h-5 w-5" />
              Critical Issues Requiring Immediate Attention
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {riskAssessment.critical_issues.map((issue, index) => (
                <li key={index} className="flex items-start gap-3 bg-white p-3 rounded border border-red-200">
                  <div className="w-2 h-2 bg-red-500 rounded-full mt-2 flex-shrink-0" />
                  <p className="text-sm text-red-800 leading-relaxed">{issue}</p>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* General Recommendations */}
      {riskAssessment.recommendations && riskAssessment.recommendations.length > 0 && (
        <Card className="border-slate-200">
          <CardHeader>
            <CardTitle className="text-slate-700 flex items-center gap-2">
              <CheckCircle className="h-5 w-5" />
              Additional Risk Mitigation Strategies
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3">
              {riskAssessment.recommendations.map((recommendation, index) => (
                <li key={index} className="flex items-start gap-3">
                  <div className="w-2 h-2 bg-slate-400 rounded-full mt-2 flex-shrink-0" />
                  <p className="text-sm text-slate-700 leading-relaxed">{recommendation}</p>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
};