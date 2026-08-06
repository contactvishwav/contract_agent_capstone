"""
TOTP MFA - standard, no-external-dependency choice (RFC 6238, the same
algorithm Google Authenticator/Authy/1Password etc. all implement), via
`pyotp`. See docs/CAPSTONE_SUMMARY.md's credential-provisioning design
report (this engagement) for why TOTP over SMS-based MFA.

Anti-replay: verify_totp_code takes the account's `last_used_step`
(infrastructure/user_repository.py's UserRepository.record_totp_step_used)
and explicitly excludes it from the set of acceptable steps, even though
it would otherwise still be within `valid_window`'s time tolerance. This
is deliberately hand-rolled instead of using pyotp's own TOTP.verify(...,
valid_window=...) directly - that call reports true/false, not *which*
step matched, and this module needs the matched step to persist for the
next call's replay check.
"""

import hmac
import secrets
import time
from dataclasses import dataclass
from typing import List, Optional

import pyotp

TOTP_ISSUER = "Contract Intelligence"
DEFAULT_VALID_WINDOW = 1  # +/- one 30s step, standard clock-skew tolerance
BACKUP_CODE_COUNT = 10


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    """otpauth:// URI - rendered as a QR code client-side (frontend), not
    generated server-side, to avoid an image-generation dependency here."""
    return pyotp.TOTP(secret, name=username, issuer=TOTP_ISSUER).provisioning_uri()


def verify_totp_code(
    secret: str, code: str, last_used_step: Optional[int], valid_window: int = DEFAULT_VALID_WINDOW,
) -> Optional[int]:
    """Returns the matched time-step if `code` is a currently-valid TOTP
    code for `secret` AND is not a replay of `last_used_step`; None
    otherwise (wrong code, expired code, or a valid-but-already-consumed
    code). Constant-time comparison (hmac.compare_digest) against each
    candidate, same discipline as any other secret comparison."""
    totp = pyotp.TOTP(secret)
    current_step = int(time.time()) // totp.interval
    for step in range(current_step - valid_window, current_step + valid_window + 1):
        if step == last_used_step:
            continue
        # TOTP.at() takes a Unix timestamp, not a raw step/counter number -
        # step * interval converts back to the timestamp that step covers.
        if hmac.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> List[str]:
    """10 single-use recovery codes (docs design report item (b), in scope
    per explicit confirmation) - human-typeable (8 hex chars, grouped),
    generated with `secrets` (cryptographically random), not `random`."""
    return [secrets.token_hex(4) for _ in range(count)]
