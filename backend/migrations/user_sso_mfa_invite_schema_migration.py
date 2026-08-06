"""
Schema migration for real credential provisioning (org invites, OIDC SSO,
TOTP MFA) - extends the User node with new optional properties and adds
the Invite node type. Mirrors user_schema_migration.py's pattern exactly:
constraints/indexes only, no data backfill needed since every new
property is optional and absent on existing accounts.

New User properties (all optional, existing password-only accounts are
untouched): email, sso_provider, sso_subject, totp_secret_encrypted,
mfa_enabled, mfa_last_used_step, backup_codes_hashed.

New Invite node: token_hash (unique), email, tenant_id, role, invited_by,
created_at, expires_at, used_at.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.infrastructure.contract_repository import Neo4jContractRepository

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


class UserSsoMfaInviteSchemaMigration:
    """Adds the Invite.token_hash uniqueness constraint and lookup indexes
    for SSO identity and invite email lookups."""

    def __init__(self):
        self.repository = Neo4jContractRepository()

    def migrate(self):
        try:
            self._add_invite_token_constraint()
            self._add_sso_identity_index()
            self._add_invite_email_index()
            logger.info("User SSO/MFA/Invite schema migration completed")
        except Exception as e:
            logger.error(f"User SSO/MFA/Invite schema migration failed: {e}")
            raise

    def _add_invite_token_constraint(self):
        query = "CREATE CONSTRAINT invite_token_hash_unique IF NOT EXISTS FOR (i:Invite) REQUIRE i.token_hash IS UNIQUE"
        try:
            self.repository.graph.query(query)
            logger.info("Invite.token_hash uniqueness constraint created")
        except Exception as e:
            logger.warning(f"Query failed (may already exist): {e}")

    def _add_sso_identity_index(self):
        # Composite index - SSO callback looks up by (provider, subject)
        # together, never by subject alone (the same subject value could
        # theoretically collide across different providers).
        query = "CREATE INDEX user_sso_identity IF NOT EXISTS FOR (u:User) ON (u.sso_provider, u.sso_subject)"
        try:
            self.repository.graph.query(query)
            logger.info("User (sso_provider, sso_subject) index created")
        except Exception as e:
            logger.warning(f"Query failed (may already exist): {e}")

    def _add_invite_email_index(self):
        query = "CREATE INDEX invite_tenant_email IF NOT EXISTS FOR (i:Invite) ON (i.tenant_id, i.email)"
        try:
            self.repository.graph.query(query)
            logger.info("Invite (tenant_id, email) index created")
        except Exception as e:
            logger.warning(f"Query failed (may already exist): {e}")


def run_migration():
    migration = UserSsoMfaInviteSchemaMigration()
    migration.migrate()


if __name__ == "__main__":
    run_migration()
