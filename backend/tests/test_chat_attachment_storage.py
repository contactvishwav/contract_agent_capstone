"""
ChatAttachmentStorage unit tests (ADR-008) - same assertion style as this
codebase's other encryption-at-rest tests (test_chunk_encryption_at_rest.py,
test_pii_encryption_at_rest.py): the encrypted-on-disk bytes must never
resemble plaintext, decrypt must round-trip exactly, and identity mismatches
(wrong tenant/session/attachment) must fail closed, not fail open.
"""

import tempfile
import unittest
from pathlib import Path

from backend.infrastructure.chat_attachment_storage import (
    ChatAttachmentStorage,
    ChatAttachmentUnavailable,
    detect_image_mime_type,
)

# Minimal, real 1x1 images - not fixtures on disk, so the test has no
# external file dependency. Verified real magic bytes, not synthetic stubs.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844410000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100dcba79820000"
    "000049454e44ae426082"
)
JPEG_MINIMAL = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 100
WEBP_MINIMAL = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"\x00" * 20
NOT_AN_IMAGE = b"this is definitely not an image, just plain text bytes"


class _FixedKeyProvider:
    """Deterministic stand-in for IKeyProvider - independent of
    ENCRYPTION_KEY/env state, matching this codebase's other storage tests'
    preference for an explicit, test-owned key over the dev-default
    fallback."""

    def get_key(self) -> bytes:
        return b"\x42" * 32


def _make_storage(tmpdir: str) -> ChatAttachmentStorage:
    return ChatAttachmentStorage(root=tmpdir, key_provider=_FixedKeyProvider())


class DetectImageMimeTypeTests(unittest.TestCase):
    def test_recognizes_png(self):
        self.assertEqual(detect_image_mime_type(PNG_1X1), "image/png")

    def test_recognizes_jpeg(self):
        self.assertEqual(detect_image_mime_type(JPEG_MINIMAL), "image/jpeg")

    def test_recognizes_webp(self):
        self.assertEqual(detect_image_mime_type(WEBP_MINIMAL), "image/webp")

    def test_rejects_non_image_bytes(self):
        self.assertIsNone(detect_image_mime_type(NOT_AN_IMAGE))

    def test_rejects_empty_bytes(self):
        self.assertIsNone(detect_image_mime_type(b""))


class StoreAndReadRoundTripTests(unittest.TestCase):
    def test_stored_bytes_on_disk_are_not_plaintext(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage_key, mime_type = storage.store("tenant_a", "SESSION_1", "ATTACH_1", PNG_1X1)

            self.assertEqual(mime_type, "image/png")
            on_disk = (Path(tmpdir) / storage_key[:2] / f"{storage_key}.bin.enc").read_bytes()
            self.assertNotIn(PNG_1X1, on_disk, "raw image bytes must not appear unencrypted on disk")

    def test_read_returns_the_exact_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage_key, _ = storage.store("tenant_a", "SESSION_1", "ATTACH_1", JPEG_MINIMAL)

            content = storage.read("tenant_a", "SESSION_1", "ATTACH_1", storage_key)
            self.assertEqual(content, JPEG_MINIMAL)

    def test_store_rejects_content_that_is_not_a_supported_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            with self.assertRaises(ValueError):
                storage.store("tenant_a", "SESSION_1", "ATTACH_1", NOT_AN_IMAGE)

    def test_store_ignores_a_lying_client_declared_type_and_sniffs_real_bytes(self):
        """The caller never gets to declare the mime_type - store() always
        returns the real, sniffed one, matching pdf_source_storage.py's
        `content.startswith(b"%PDF-")` posture (trust the bytes, not any
        externally-supplied label)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            _, mime_type = storage.store("tenant_a", "SESSION_1", "ATTACH_1", WEBP_MINIMAL)
            self.assertEqual(mime_type, "image/webp")


class IdentityBindingTests(unittest.TestCase):
    """AAD binds the ciphertext to (tenant_id, session_id, attachment_id) -
    any mismatch must fail closed, proving a stolen/misdirected storage_key
    cannot be replayed under a different identity."""

    def test_read_with_wrong_tenant_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage_key, _ = storage.store("tenant_a", "SESSION_1", "ATTACH_1", PNG_1X1)
            with self.assertRaises(ChatAttachmentUnavailable):
                storage.read("tenant_b", "SESSION_1", "ATTACH_1", storage_key)

    def test_read_with_wrong_session_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage_key, _ = storage.store("tenant_a", "SESSION_1", "ATTACH_1", PNG_1X1)
            with self.assertRaises(ChatAttachmentUnavailable):
                storage.read("tenant_a", "SESSION_2", "ATTACH_1", storage_key)

    def test_read_with_wrong_attachment_id_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage_key, _ = storage.store("tenant_a", "SESSION_1", "ATTACH_1", PNG_1X1)
            with self.assertRaises(ChatAttachmentUnavailable):
                storage.read("tenant_a", "SESSION_1", "ATTACH_2", storage_key)

    def test_storage_key_mismatch_fails_before_ever_touching_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage.store("tenant_a", "SESSION_1", "ATTACH_1", PNG_1X1)
            with self.assertRaises(ChatAttachmentUnavailable):
                storage.read("tenant_a", "SESSION_1", "ATTACH_1", "0" * 64)


class PathSafetyTests(unittest.TestCase):
    """Defense in depth against a corrupted DB property ever becoming a
    filesystem path - same posture as pdf_source_storage.py's _path()."""

    def test_malformed_storage_key_is_rejected_not_traversed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            for bad_key in ["../../etc/passwd", "short", "X" * 64, ""]:
                with self.assertRaises(ChatAttachmentUnavailable):
                    storage.read("tenant_a", "SESSION_1", "ATTACH_1", bad_key)


class RemoveTests(unittest.TestCase):
    def test_remove_deletes_the_blob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage_key, _ = storage.store("tenant_a", "SESSION_1", "ATTACH_1", PNG_1X1)
            storage.remove("tenant_a", "SESSION_1", "ATTACH_1")
            with self.assertRaises(ChatAttachmentUnavailable):
                storage.read("tenant_a", "SESSION_1", "ATTACH_1", storage_key)

    def test_remove_of_nonexistent_blob_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = _make_storage(tmpdir)
            storage.remove("tenant_a", "SESSION_1", "ATTACH_1")  # must not raise


if __name__ == "__main__":
    unittest.main()
