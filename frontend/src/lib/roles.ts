// Shared UserRole list (backend/governance/rbac.py's UserRole enum) -
// previously only defined inline in LoginScreen.tsx; extracted so
// AccountPage.tsx's invite form doesn't duplicate (and risk drifting
// from) the same list.
export const ROLES = [
  { value: 'ADMIN', label: 'Admin', description: 'Full access, including policy management and audit trail' },
  { value: 'LEGAL_REVIEWER', label: 'Legal Reviewer', description: 'Upload and analyze contracts, view reports' },
  { value: 'AUDITOR', label: 'Auditor', description: 'View reports and audit trail only' },
  { value: 'VIEWER', label: 'Viewer', description: 'Analyze/query only' },
];
