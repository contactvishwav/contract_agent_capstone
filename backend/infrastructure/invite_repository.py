"""
Org invite tokens - the actual provisioning gate real credential
provisioning (this engagement's design report) replaces open self-service
registration with. An invite is what authorizes joining an
ALREADY-provisioned tenant; api/auth_api.py's POST /register remains only
for bootstrapping a brand-new tenant's first user
(UserRepository.create_user's enforce_tenant_bootstrap).

Token storage: sha256, not bcrypt. A password is low-entropy and
user-chosen, which is exactly what bcrypt's adaptive cost defends against
(slow, deliberately expensive brute-force resistance for a small keyspace).
An invite token is secrets.token_urlsafe(32) - 256 bits of real randomness,
already computationally infeasible to brute-force regardless of hash
speed - so bcrypt's slowness buys nothing here and sha256 (fast, standard,
appropriate for high-entropy token hashing - the same category as API-key
hashing) is the right primitive. Still never stored in plaintext: a raw
token leaked from a DB backup must not be directly usable.

Single-use enforcement: consume_invite's MATCH ... WHERE used_at IS NULL
... SET used_at = ... executes as one atomic Cypher write - two concurrent
accept attempts against the same invite cannot both see used_at IS NULL
and succeed, the same "let the database guarantee it, not an application-
level check-then-act" discipline as User.username's uniqueness constraint.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_INVITE_TTL_DAYS = 7


@dataclass
class InviteRecord:
    email: str
    tenant_id: str
    role: str
    invited_by: str
    expires_at: datetime
    used_at: Optional[datetime]


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class InviteRepository:
    def __init__(self, graph_=None):
        self.graph = graph_ or graph

    def create_invite(
        self, email: str, tenant_id: str, role: str, invited_by: str, ttl_days: int = DEFAULT_INVITE_TTL_DAYS,
    ) -> str:
        """Returns the raw token (shown/emailed exactly once - only its
        hash is ever persisted, same one-way-street as a password)."""
        email = email.strip().lower()
        if not email:
            raise ValueError("email must not be empty")
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

        self.graph.query(
            """
            CREATE (i:Invite {
                token_hash: $token_hash,
                email: $email,
                tenant_id: $tenant_id,
                role: $role,
                invited_by: $invited_by,
                created_at: datetime(),
                expires_at: datetime($expires_at),
                used_at: null
            })
            """,
            {
                "token_hash": token_hash,
                "email": email,
                "tenant_id": tenant_id,
                "role": role,
                "invited_by": invited_by,
                "expires_at": expires_at.isoformat(),
            },
        )
        logger.info(f"Created invite for '{email}' to tenant '{tenant_id}' as '{role}' (by '{invited_by}')")
        return raw_token

    def get_invite(self, raw_token: str) -> Optional[InviteRecord]:
        """Preview only - does NOT consume the invite. Used by the
        frontend to show "you're invited to join <tenant> as <role>"
        before the invitee commits to a password. Returns None for an
        unknown, expired, or already-used token - deliberately the same
        generic "not found" for all three, so a caller can't distinguish
        "never existed" from "expired" from "already used" by probing."""
        result = self.graph.query(
            "MATCH (i:Invite {token_hash: $token_hash}) RETURN i.email as email, i.tenant_id as tenant_id, "
            "i.role as role, i.invited_by as invited_by, i.expires_at as expires_at, i.used_at as used_at",
            {"token_hash": _hash_token(raw_token)},
        )
        if not result:
            return None
        row = result[0]
        record = _row_to_record(row)
        if record.used_at is not None or record.expires_at < datetime.now(timezone.utc):
            return None
        return record

    def consume_invite(self, raw_token: str) -> Optional[InviteRecord]:
        """Atomically marks the invite used and returns its record, or
        None if the token is unknown, already used, or expired (checked
        inside the same WHERE that gates the SET, not as a separate
        read-then-write) - the single-use guarantee this whole mechanism
        depends on."""
        token_hash = _hash_token(raw_token)
        result = self.graph.query(
            """
            MATCH (i:Invite {token_hash: $token_hash})
            WHERE i.used_at IS NULL AND i.expires_at > datetime($now)
            SET i.used_at = datetime()
            RETURN i.email as email, i.tenant_id as tenant_id, i.role as role,
                   i.invited_by as invited_by, i.expires_at as expires_at, i.used_at as used_at
            """,
            {"token_hash": token_hash, "now": datetime.now(timezone.utc).isoformat()},
        )
        if not result:
            return None
        return _row_to_record(result[0])


def _row_to_record(row: dict) -> InviteRecord:
    expires_at = row["expires_at"]
    used_at = row.get("used_at")
    return InviteRecord(
        email=row["email"],
        tenant_id=row["tenant_id"],
        role=row["role"],
        invited_by=row["invited_by"],
        expires_at=_to_datetime(expires_at),
        used_at=_to_datetime(used_at) if used_at else None,
    )


def _to_datetime(value) -> datetime:
    """Neo4j datetime properties round-trip through the driver as either a
    native datetime, a neo4j.time.DateTime (has .to_native()), or (in
    tests, via the plain-dict FakeInviteGraph) an ISO string - normalize
    all three to a real, timezone-aware datetime.datetime."""
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    if hasattr(value, "to_native"):
        return value.to_native()
    return value
