"""Encrypted, opaque storage for original tenant-owned source PDFs.

The storage key is derived server-side from tenant and contract identity and is
never accepted from an HTTP caller.  AES-GCM additional authenticated data binds
the ciphertext to that same identity, so copying a blob to another key cannot
make it decrypt as another tenant's document.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.infrastructure.encryption import IKeyProvider, get_key_provider


PDF_SOURCE_STORAGE_VERSION = "pdf-source-aesgcm-v1"
DEFAULT_PDF_SOURCE_STORAGE_DIR = "/var/lib/contract-agent/source-pdfs"


class PdfSourceUnavailable(FileNotFoundError):
    """The authorized contract has no readable original PDF blob."""


class PdfSourceStorage:
    def __init__(
        self,
        root: Optional[str] = None,
        key_provider: Optional[IKeyProvider] = None,
    ):
        self.root = Path(
            root or os.getenv("PDF_SOURCE_STORAGE_DIR", DEFAULT_PDF_SOURCE_STORAGE_DIR)
        )
        self._key_provider = key_provider or get_key_provider()

    @staticmethod
    def storage_key(tenant_id: str, contract_id: str) -> str:
        material = f"{PDF_SOURCE_STORAGE_VERSION}:{tenant_id}:{contract_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _aad(tenant_id: str, contract_id: str) -> bytes:
        return f"{PDF_SOURCE_STORAGE_VERSION}:{tenant_id}:{contract_id}".encode("utf-8")

    def _path(self, storage_key: str) -> Path:
        # Defense in depth against a corrupted DB property ever becoming a
        # filesystem path.  HTTP callers never supply this value.
        if len(storage_key) != 64 or any(char not in "0123456789abcdef" for char in storage_key):
            raise PdfSourceUnavailable("Source PDF is unavailable")
        return self.root / storage_key[:2] / f"{storage_key}.pdf.enc"

    def store(self, tenant_id: str, contract_id: str, content: bytes) -> str:
        if not content.startswith(b"%PDF-"):
            raise ValueError("Source content is not a PDF")
        storage_key = self.storage_key(tenant_id, contract_id)
        target = self._path(storage_key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key_provider.get_key()).encrypt(
            nonce,
            content,
            self._aad(tenant_id, contract_id),
        )
        payload = PDF_SOURCE_STORAGE_VERSION.encode("ascii") + b"\n" + nonce + ciphertext

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
        return storage_key

    def read(self, tenant_id: str, contract_id: str, storage_key: str) -> bytes:
        expected_key = self.storage_key(tenant_id, contract_id)
        if storage_key != expected_key:
            raise PdfSourceUnavailable("Source PDF is unavailable")
        path = self._path(storage_key)
        try:
            payload = path.read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise PdfSourceUnavailable("Source PDF is unavailable") from exc

        prefix = PDF_SOURCE_STORAGE_VERSION.encode("ascii") + b"\n"
        if not payload.startswith(prefix) or len(payload) <= len(prefix) + 12:
            raise PdfSourceUnavailable("Source PDF is unavailable")
        encrypted = payload[len(prefix):]
        nonce, ciphertext = encrypted[:12], encrypted[12:]
        try:
            content = AESGCM(self._key_provider.get_key()).decrypt(
                nonce,
                ciphertext,
                self._aad(tenant_id, contract_id),
            )
        except Exception as exc:
            raise PdfSourceUnavailable("Source PDF is unavailable") from exc
        if not content.startswith(b"%PDF-"):
            raise PdfSourceUnavailable("Source PDF is unavailable")
        return content

    def remove(self, tenant_id: str, contract_id: str) -> None:
        """Remove one exact blob; used only to roll back a failed new write."""
        path = self._path(self.storage_key(tenant_id, contract_id))
        try:
            path.unlink()
        except FileNotFoundError:
            return


pdf_source_storage = PdfSourceStorage()
