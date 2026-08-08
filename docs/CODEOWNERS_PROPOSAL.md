# CODEOWNERS and branch-protection proposal

Repository owner identities are not established, so this is a role-based proposal—not an active `.github/CODEOWNERS` file. Replace placeholders only with confirmed GitHub teams.

```text
# Replace each placeholder with a real team, then install as .github/CODEOWNERS
*                                      @ORG/maintainers
/backend/governance/                   @ORG/security-tenancy
/backend/api/auth_api.py               @ORG/security-tenancy
/backend/api/sso_api.py                @ORG/security-tenancy
/backend/mcp_server.py                 @ORG/security-tenancy @ORG/mcp
/backend/mcp/                          @ORG/security-tenancy @ORG/mcp
/backend/agents/planning/              @ORG/analysis-engine
/backend/agents/contract_intelligence_agents.py @ORG/analysis-engine
/backend/infrastructure/               @ORG/graph-data
/backend/migrations/                   @ORG/graph-data @ORG/security-tenancy
/frontend/                             @ORG/frontend
/AGENTS.md                             @ORG/architecture @ORG/security-tenancy
/docs/adr/                             @ORG/architecture
/docs/EVALUATION.md                    @ORG/model-evaluation
/docs/DEPLOYMENT.md                    @ORG/platform
```

## Branch protection

For `main`: require PRs, at least one review plus required CODEOWNER review for matched areas, conversation resolution, up-to-date branches, no force-push/deletion, signed or verified commits if organizationally available, and required status checks. Initially require only checks with a clean established baseline: backend tests with Neo4j, blocking Python E9/F821, frontend type-check/build once added, and governance link/secret checks once implemented. Keep baseline-debt checks report-only until triaged.

Security/tenancy, migrations, default/fallback routing, model evaluation claims, and production manifests should require their specialist role. Emergency bypass must be audited and followed by a retrospective PR.
