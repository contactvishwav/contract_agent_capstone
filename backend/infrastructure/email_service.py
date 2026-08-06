"""
Transactional email for org invites, via Resend's HTTP API directly (no
SDK dependency - one small POST, easy to mock in tests, no risk of an
SDK's own API shape drifting from what's documented).

Free tier (verified live against Resend's real API during this
engagement, Aug 2026): 3,000 emails/month, no credit card. Without a
verified sending domain, INVITE_EMAIL_FROM must stay Resend's sandbox
address (onboarding@resend.dev), which can only deliver to the Resend
account owner's own verified email - enough to prove the whole invite
flow end-to-end, but not enough to invite arbitrary teammates. Verifying
a real domain with Resend (DNS records) is the natural next step for that,
out of scope here.

RESEND_API_KEY absent or rejected degrades to send()'s return value
carrying `sent: False` rather than raising - invite CREATION (the
database record + accept link) must succeed even if the email happens to
fail to send, since the invite is still usable if handed to the invitee
through any other channel (Slack, a direct link) - the same
degrade-don't-crash posture as this codebase's other external-call
wrappers (LLMExtractionService, get_default_llm).
"""

import os
from dataclasses import dataclass

import httpx

from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


@dataclass
class EmailSendResult:
    sent: bool
    reason: str = ""
    resend_id: str = ""


class EmailService:
    def __init__(self, api_key: str = None, from_address: str = None):
        self.api_key = api_key or os.getenv("RESEND_API_KEY")
        self.from_address = from_address or os.getenv("INVITE_EMAIL_FROM", "onboarding@resend.dev")

    def send_invite_email(self, to_email: str, tenant_id: str, role: str, accept_url: str) -> EmailSendResult:
        if not self.api_key:
            logger.warning("RESEND_API_KEY not configured - invite created but no email sent")
            return EmailSendResult(sent=False, reason="RESEND_API_KEY not configured")

        subject = f"You're invited to join {tenant_id}"
        html = (
            f"<p>You've been invited to join <strong>{tenant_id}</strong> as <strong>{role}</strong>.</p>"
            f"<p><a href=\"{accept_url}\">Accept your invite</a></p>"
            f"<p>This link expires in 7 days. If you weren't expecting this, you can ignore this email.</p>"
        )

        try:
            response = httpx.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"from": self.from_address, "to": [to_email], "subject": subject, "html": html},
                timeout=10.0,
            )
        except httpx.HTTPError as e:
            logger.error(f"Invite email to '{to_email}' failed - network error: {e}")
            return EmailSendResult(sent=False, reason=str(e))

        if response.status_code >= 400:
            logger.error(f"Invite email to '{to_email}' failed - Resend {response.status_code}: {response.text}")
            return EmailSendResult(sent=False, reason=f"Resend {response.status_code}: {response.text}")

        resend_id = response.json().get("id", "")
        logger.info(f"Invite email sent to '{to_email}' for tenant '{tenant_id}' (Resend id: {resend_id})")
        return EmailSendResult(sent=True, resend_id=resend_id)
