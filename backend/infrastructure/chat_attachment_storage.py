"""Encrypted, opaque storage for Contract Chat image attachments.

Mirrors backend/infrastructure/pdf_source_storage.py's AES-GCM pattern
exactly - same security discipline as source PDFs, applied to chat images:
the storage key is derived server-side from tenant/session/attachment
identity and is never accepted from an HTTP caller, and AES-GCM additional
authenticated data binds the ciphertext to that same identity, so copying a
blob to another key cannot make it decrypt as another tenant's/session's
attachment.

Bound to (tenant_id, session_id, attachment_id) rather than PDF storage's
(tenant_id, contract_id): chat attachments are session-scoped conversation
artifacts, not tenant-wide canonical documents, so the tighter binding
matches what actually identifies one. See ADR-008 for the full design.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.infrastructure.encryption import IKeyProvider, get_key_provider


CHAT_ATTACHMENT_STORAGE_VERSION = "chat-attachment-aesgcm-v1"
DEFAULT_CHAT_ATTACHMENT_STORAGE_DIR = "/var/lib/contract-agent/chat-attachments"

# Real image-format magic bytes, not the client-declared Content-Type - same
# "trust the bytes, not the header" posture as pdf_source_storage.py's
# `content.startswith(b"%PDF-")` check. GIF is deliberately excluded (see
# ADR-008): Anthropic doesn't handle animated frames the same way Gemini/
# OpenAI do, and a narrower, uniformly-safe allowlist beats a wider one with
# per-provider surprises.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"


def detect_image_mime_type(content: bytes) -> Optional[str]:
    """Real, sniffed image format - None if it isn't one of the three
    supported types, regardless of what any caller claims it is."""
    if content.startswith(_PNG_SIGNATURE):
        return "image/png"
    if content.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    if len(content) >= 12 and content[0:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


class ChatAttachmentUnavailable(FileNotFoundError):
    """The authorized attachment has no readable encrypted blob."""


class ChatAttachmentStorage:
    def __init__(
        self,
        root: Optional[str] = None,
        key_provider: Optional[IKeyProvider] = None,
    ):
        target_root = root or os.getenv("CHAT_ATTACHMENT_STORAGE_DIR")
        if not target_root:
            default_path = Path(DEFAULT_CHAT_ATTACHMENT_STORAGE_DIR)
            try:
                default_path.mkdir(parents=True, exist_ok=True)
                target_root = str(default_path)
            except (PermissionError, OSError):
                fallback_path = Path.cwd() / "data" / "chat-attachments"
                fallback_path.mkdir(parents=True, exist_ok=True)
                target_root = str(fallback_path)
        self.root = Path(target_root)
        self._key_provider = key_provider or get_key_provider()

    @staticmethod
    def storage_key(tenant_id: str, session_id: str, attachment_id: str) -> str:
        material = f"{CHAT_ATTACHMENT_STORAGE_VERSION}:{tenant_id}:{session_id}:{attachment_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _aad(tenant_id: str, session_id: str, attachment_id: str) -> bytes:
        return f"{CHAT_ATTACHMENT_STORAGE_VERSION}:{tenant_id}:{session_id}:{attachment_id}".encode("utf-8")

    def _path(self, storage_key: str) -> Path:
        # Defense in depth against a corrupted DB property ever becoming a
        # filesystem path. HTTP callers never supply this value.
        if len(storage_key) != 64 or any(char not in "0123456789abcdef" for char in storage_key):
            raise ChatAttachmentUnavailable("Attachment is unavailable")
        return self.root / storage_key[:2] / f"{storage_key}.bin.enc"

    def store(self, tenant_id: str, session_id: str, attachment_id: str, content: bytes) -> Tuple[str, str]:
        """Returns (storage_key, mime_type). Raises ValueError if the real
        bytes don't match one of the supported image formats - the caller's
        declared Content-Type is never trusted for this decision."""
        mime_type = detect_image_mime_type(content)
        if not mime_type:
            raise ValueError("Attachment content is not a supported image format")

        storage_key = self.storage_key(tenant_id, session_id, attachment_id)
        target = self._path(storage_key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key_provider.get_key()).encrypt(
            nonce,
            content,
            self._aad(tenant_id, session_id, attachment_id),
        )
        payload = CHAT_ATTACHMENT_STORAGE_VERSION.encode("ascii") + b"\n" + nonce + ciphertext

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{storage_key}.",
            suffix=".tmp",
            dir=target.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return storage_key, mime_type

    def read(self, tenant_id: str, session_id: str, attachment_id: str, storage_key: str) -> bytes:
        expected_key = self.storage_key(tenant_id, session_id, attachment_id)
        if storage_key != expected_key:
            raise ChatAttachmentUnavailable("Attachment is unavailable")
        path = self._path(storage_key)
        try:
            payload = path.read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise ChatAttachmentUnavailable("Attachment is unavailable") from exc

        prefix = CHAT_ATTACHMENT_STORAGE_VERSION.encode("ascii") + b"\n"
        if not payload.startswith(prefix) or len(payload) <= len(prefix) + 12:
            raise ChatAttachmentUnavailable("Attachment is unavailable")
        encrypted = payload[len(prefix):]
        nonce, ciphertext = encrypted[:12], encrypted[12:]
        try:
            content = AESGCM(self._key_provider.get_key()).decrypt(
                nonce,
                ciphertext,
                self._aad(tenant_id, session_id, attachment_id),
            )
        except Exception as exc:
            raise ChatAttachmentUnavailable("Attachment is unavailable") from exc
        if not detect_image_mime_type(content):
            raise ChatAttachmentUnavailable("Attachment is unavailable")
        return content

    def remove(self, tenant_id: str, session_id: str, attachment_id: str) -> None:
        """Remove one exact blob; used only to roll back a failed graph write."""
        path = self._path(self.storage_key(tenant_id, session_id, attachment_id))
        try:
            path.unlink()
        except FileNotFoundError:
            return


chat_attachment_storage = ChatAttachmentStorage()
