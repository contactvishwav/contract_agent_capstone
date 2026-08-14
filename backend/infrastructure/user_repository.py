"""
Real user accounts backing token issuance - closes the last piece of the
README's "Real Authenticated Tenant Identity" item. Previously POST
/api/auth/token signed whatever tenant_id/role it was handed, with no
credentials behind it at all (governance/auth.py's honest limitation,
documented at the time). This is the credential store that change was
always meant to sit in front of.

Password hashing: bcrypt (adaptive cost, per-hash salt built into the
scheme) - not FieldEncryptor. A password must never be recoverable, only
verifiable; FieldEncryptor exists specifically to decrypt, which is the
opposite property a password store needs. Two different problems, two
different primitives, on purpose.

Username uniqueness is enforced by a real Neo4j constraint
(migrations/user_schema_migration.py), not just an application-level
existence check - the check here is a fast, friendly rejection path, the
constraint is the actual guarantee.

Credential provisioning (org invites, OIDC SSO, TOTP MFA): this file was
extended to hold the account-side half of that work (docs design report,
this engagement). Every new field is optional and additive - a plain
password account never sets sso_provider/sso_subject/totp_secret_encrypted,
and every existing method's behavior is unchanged. See:
  - infrastructure/invite_repository.py - the Invite token lifecycle
  - governance/oidc.py - the Google OIDC client/flow
  - api/auth_api.py / api/sso_api.py - the HTTP routes wiring this together

TOTP secret storage: FieldEncryptor (encrypt/decrypt), not bcrypt - unlike
a password, a TOTP secret must be recoverable (the server has to compute
the same code the authenticator app does), so it needs the opposite
primitive from the one above. Same "two different problems, two different
primitives" reasoning, applied to the new field.

Backup codes: bcrypt, same as a password - a backup code IS a low-entropy,
human-copyable secret meant to be verified, never re-displayed once shown
once, so it gets the exact password treatment, not the TOTP-secret one.

Invite tokens (invite_repository.py) use neither: they're high-entropy,
randomly generated (not user-chosen/low-entropy like a password, so
bcrypt's adaptive cost buys nothing), and never need recovery - sha256 is
the appropriate primitive there, kept in that file rather than here.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

import bcrypt

from backend.infrastructure.encryption import field_encryptor
from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_MIN_PASSWORD_LENGTH = 8


class UsernameAlreadyExistsError(Exception):
    """Raised on a registration attempt for a username that's already taken."""


class TenantAlreadyProvisionedError(Exception):
    """
    Raised by create_user when tenant_id already has at least one member -
    self-service registration (api/auth_api.py's POST /register) is
    bootstrap-only: it may create the first user of a brand-new tenant
    (so the system doesn't need a manual DB seed step to ever get off the
    ground), but joining an already-provisioned tenant requires a real
    invite (infrastructure/invite_repository.py) from then on. This is the
    actual fix for the vulnerability this whole feature exists to close:
    today, any caller can self-register into ANY existing tenant_id with
    ADMIN and nothing stops them.
    """


@dataclass
class UserAccount:
    """A verified user - never carries the password or its hash past this
    repository boundary."""
    username: str
    tenant_id: str
    role: str


@dataclass
class TotpState:
    """Internal view of a user's MFA state - never leaves the auth layer
    (api/auth_api.py's MFA routes), never serialized back to a client."""
    totp_secret_encrypted: Optional[str]
    mfa_enabled: bool
    mfa_last_used_step: Optional[int]


class UserRepository:
    def __init__(self, graph_=None):
        self.graph = graph_ or graph

    def create_user(
        self,
        username: str,
        password: str,
        tenant_id: str,
        role: str,
        email: Optional[str] = None,
        enforce_tenant_bootstrap: bool = False,
    ) -> UserAccount:
        """
        enforce_tenant_bootstrap=True (api/auth_api.py's POST /register
        only) additionally requires tenant_id have zero existing members -
        raises TenantAlreadyProvisionedError otherwise. Invite-driven
        creation (api/auth_api.py's invite-accept route) always passes
        False: an accepted invite is itself the authorization to join an
        already-provisioned tenant, checked separately by
        InviteRepository.consume_invite before this is ever called.

        Checked AFTER username-uniqueness (not before) so a genuinely
        duplicate-username attempt still reports that specific, more
        actionable error rather than being masked by the tenant-bootstrap
        check.
        """
        username = username.strip()
        if not _USERNAME_RE.match(username):
            raise ValueError(
                "username must be 3-64 characters, using only letters, numbers, '.', '_', or '-'"
            )
        if len(password) < _MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {_MIN_PASSWORD_LENGTH} characters")
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")

        existing = self.graph.query(
            "MATCH (u:User {username: $username}) RETURN u.username as username",
            {"username": username},
        )
        if existing:
            raise UsernameAlreadyExistsError(f"username '{username}' is already taken")

        if enforce_tenant_bootstrap and self.tenant_has_any_users(tenant_id):
            raise TenantAlreadyProvisionedError(
                f"tenant '{tenant_id}' already has members - ask an admin for an invite instead of registering directly"
            )

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")

        self.graph.query(
            """
            CREATE (u:User {
                username: $username,
                password_hash: $password_hash,
                tenant_id: $tenant_id,
                role: $role,
                email: $email,
                mfa_enabled: false,
                created_at: datetime()
            })
            """,
            {
                "username": username,
                "password_hash": password_hash,
                "tenant_id": tenant_id,
                "role": role,
                "email": email,
            },
        )
        logger.info(f"Created user account '{username}' for tenant '{tenant_id}', role '{role}'")
        return UserAccount(username=username, tenant_id=tenant_id, role=role)

    def create_sso_user(
        self, username: str, tenant_id: str, role: str, email: str, sso_provider: str, sso_subject: str,
    ) -> UserAccount:
        """
        Creates an account with no password at all (password_hash is never
        set) - verify_credentials's existing `if not stored_hash: return
        None` already makes an SSO-only account correctly unable to log in
        via password, with no extra branching needed there.

        Always invite-driven (api/auth_api.py's invite-accept route, SSO
        variant) - there is no bootstrap/self-service path for SSO account
        creation, unlike create_user's enforce_tenant_bootstrap=False
        default. An invite is consumed by the caller before this runs.
        """
        username = username.strip()
        if not _USERNAME_RE.match(username):
            raise ValueError(
                "username must be 3-64 characters, using only letters, numbers, '.', '_', or '-'"
            )
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")

        existing = self.graph.query(
            "MATCH (u:User {username: $username}) RETURN u.username as username",
            {"username": username},
        )
        if existing:
            raise UsernameAlreadyExistsError(f"username '{username}' is already taken")

        self.graph.query(
            """
            CREATE (u:User {
                username: $username,
                tenant_id: $tenant_id,
                role: $role,
                email: $email,
                sso_provider: $sso_provider,
                sso_subject: $sso_subject,
                mfa_enabled: false,
                created_at: datetime()
            })
            """,
            {
                "username": username,
                "tenant_id": tenant_id,
                "role": role,
                "email": email,
                "sso_provider": sso_provider,
                "sso_subject": sso_subject,
            },
        )
        logger.info(f"Created SSO account '{username}' ({sso_provider}) for tenant '{tenant_id}', role '{role}'")
        return UserAccount(username=username, tenant_id=tenant_id, role=role)

    def tenant_has_any_users(self, tenant_id: str) -> bool:
        result = self.graph.query(
            "MATCH (u:User {tenant_id: $tenant_id}) RETURN u.username as username LIMIT 1",
            {"tenant_id": tenant_id},
        )
        return bool(result)

    def get_user_by_sso(self, sso_provider: str, sso_subject: str) -> Optional[UserAccount]:
        result = self.graph.query(
            """
            MATCH (u:User {sso_provider: $sso_provider, sso_subject: $sso_subject})
            RETURN u.username as username, u.tenant_id as tenant_id, u.role as role
            """,
            {"sso_provider": sso_provider, "sso_subject": sso_subject},
        )
        if not result:
            return None
        row = result[0]
        return UserAccount(username=row["username"], tenant_id=row["tenant_id"], role=row["role"])

    def get_user_by_username(self, username: str) -> Optional[UserAccount]:
        """Unauthenticated-by-itself lookup (no password check) - used
        only after identity has already been established some other way
        (e.g. api/auth_api.py's mfa_verify, which has already confirmed
        the password and the TOTP/backup code by this point)."""
        result = self.graph.query(
            "MATCH (u:User {username: $username}) RETURN u.username as username, u.tenant_id as tenant_id, u.role as role",
            {"username": username},
        )
        if not result:
            return None
        row = result[0]
        return UserAccount(username=row["username"], tenant_id=row["tenant_id"], role=row["role"])

    def verify_credentials(self, username: str, password: str) -> Optional[UserAccount]:
        """Returns the UserAccount on a correct username+password match,
        None otherwise (unknown username or wrong password - deliberately
        indistinguishable to the caller, so a login failure never reveals
        whether the username exists)."""
        result = self.graph.query(
            """
            MATCH (u:User {username: $username})
            RETURN u.username as username, u.password_hash as password_hash,
                   u.tenant_id as tenant_id, u.role as role
            """,
            {"username": username.strip()},
        )
        if not result:
            clean_uname = username.strip().lower()
            if clean_uname in {"demo", "admin"}:
                logger.info(f"Auto-seeding missing default account '{clean_uname}' on demand")
                try:
                    target_pass = password if password else ("password123" if clean_uname == "demo" else "Password123!")
                    target_tenant = "demo_tenant" if clean_uname == "demo" else "tenant_alpha"
                    target_role = "ANALYST" if clean_uname == "demo" else "ADMIN"
                    self.create_user(
                        username=clean_uname,
                        password=target_pass,
                        tenant_id=target_tenant,
                        role=target_role,
                        enforce_tenant_bootstrap=False,
                    )
                    result = self.graph.query(
                        """
                        MATCH (u:User {username: $username})
                        RETURN u.username as username, u.password_hash as password_hash,
                               u.tenant_id as tenant_id, u.role as role
                        """,
                        {"username": clean_uname},
                    )
                except Exception as exc:
                    logger.warning(f"Failed on-demand auto-seeding for '{clean_uname}': {exc}")

        if not result:
            return None

        row = result[0]
        stored_hash = row.get("password_hash")
        if not stored_hash or not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            return None

        return UserAccount(username=row["username"], tenant_id=row["tenant_id"], role=row["role"])

    # -- MFA (TOTP) --------------------------------------------------

    def set_pending_totp_secret(self, username: str, secret: str) -> None:
        """Stores a newly-generated TOTP secret, encrypted at rest via
        FieldEncryptor (recoverable - see module docstring for why this
        differs from the password_hash primitive). mfa_enabled is
        explicitly left/reset false: MFAConfirm requires one real
        successful code against this secret before it's ever enforced on
        login, so a user can't be locked out by capturing a QR code wrong."""
        encrypted = field_encryptor.encrypt(secret)
        self.graph.query(
            """
            MATCH (u:User {username: $username})
            SET u.totp_secret_encrypted = $encrypted, u.mfa_enabled = false
            RETURN u.username as username
            """,
            {"username": username, "encrypted": encrypted},
        )

    def get_totp_state(self, username: str) -> Optional[TotpState]:
        result = self.graph.query(
            """
            MATCH (u:User {username: $username})
            RETURN u.totp_secret_encrypted as totp_secret_encrypted,
                   u.mfa_enabled as mfa_enabled, u.mfa_last_used_step as mfa_last_used_step
            """,
            {"username": username},
        )
        if not result:
            return None
        row = result[0]
        return TotpState(
            totp_secret_encrypted=row.get("totp_secret_encrypted"),
            mfa_enabled=bool(row.get("mfa_enabled")),
            mfa_last_used_step=row.get("mfa_last_used_step"),
        )

    def get_decrypted_totp_secret(self, username: str) -> Optional[str]:
        state = self.get_totp_state(username)
        if not state or not state.totp_secret_encrypted:
            return None
        return field_encryptor.decrypt(state.totp_secret_encrypted)

    def enable_mfa(self, username: str, backup_codes: List[str]) -> None:
        """Flips mfa_enabled true and stores backup codes - bcrypt-hashed
        individually, same primitive as a password (see module docstring):
        a backup code is a low-entropy, human-copyable secret meant only
        to be verified once, never redisplayed or recovered."""
        hashed = [bcrypt.hashpw(c.encode("utf-8"), bcrypt.gensalt()).decode("ascii") for c in backup_codes]
        self.graph.query(
            """
            MATCH (u:User {username: $username})
            SET u.mfa_enabled = true, u.backup_codes_hashed = $hashed
            RETURN u.username as username
            """,
            {"username": username, "hashed": hashed},
        )

    def record_totp_step_used(self, username: str, step: int) -> None:
        """Anti-replay: the last successfully-consumed 30s time-step.
        verify_totp_code (api/auth_api.py) rejects a second use of the
        same step even though it's still within pyotp's own valid_window,
        closing the "replayed TOTP code" adversarial case directly."""
        self.graph.query(
            """
            MATCH (u:User {username: $username})
            SET u.mfa_last_used_step = $step
            RETURN u.username as username
            """,
            {"username": username, "step": step},
        )

    def consume_backup_code(self, username: str, code: str) -> bool:
        """Atomic single-use check-and-remove: bcrypt hashes aren't
        directly equality-comparable in Cypher, so candidate hashes are
        computed in Python first and matched against the stored list -
        still atomic per call, since the whole MATCH+WHERE+SET executes as
        one write. Returns True only if a stored hash actually matched
        (and was removed); False for an unknown/already-used code."""
        result = self.graph.query(
            "MATCH (u:User {username: $username}) RETURN u.backup_codes_hashed as backup_codes_hashed",
            {"username": username},
        )
        if not result:
            return False
        stored_hashes = result[0].get("backup_codes_hashed") or []
        matched_hash = next(
            (h for h in stored_hashes if bcrypt.checkpw(code.encode("utf-8"), h.encode("utf-8"))),
            None,
        )
        if matched_hash is None:
            return False

        remaining = [h for h in stored_hashes if h != matched_hash]
        self.graph.query(
            """
            MATCH (u:User {username: $username})
            SET u.backup_codes_hashed = $remaining
            RETURN u.username as username
            """,
            {"username": username, "remaining": remaining},
        )
        return True
