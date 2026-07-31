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
"""

import re
from dataclasses import dataclass
from typing import Optional

import bcrypt

from backend.shared.utils.contract_search_tool import graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_MIN_PASSWORD_LENGTH = 8


class UsernameAlreadyExistsError(Exception):
    """Raised on a registration attempt for a username that's already taken."""


@dataclass
class UserAccount:
    """A verified user - never carries the password or its hash past this
    repository boundary."""
    username: str
    tenant_id: str
    role: str


class UserRepository:
    def __init__(self, graph_=None):
        self.graph = graph_ or graph

    def create_user(self, username: str, password: str, tenant_id: str, role: str) -> UserAccount:
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

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")

        self.graph.query(
            """
            CREATE (u:User {
                username: $username,
                password_hash: $password_hash,
                tenant_id: $tenant_id,
                role: $role,
                created_at: datetime()
            })
            """,
            {
                "username": username,
                "password_hash": password_hash,
                "tenant_id": tenant_id,
                "role": role,
            },
        )
        logger.info(f"Created user account '{username}' for tenant '{tenant_id}', role '{role}'")
        return UserAccount(username=username, tenant_id=tenant_id, role=role)

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
            return None

        row = result[0]
        stored_hash = row.get("password_hash")
        if not stored_hash or not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            return None

        return UserAccount(username=row["username"], tenant_id=row["tenant_id"], role=row["role"])
