#!/bin/sh
# Runs as root (see Dockerfile - USER appuser was removed so this can run),
# fixes ownership of the chat-attachments volume mount, then drops to
# appuser for the real process. Needed because a Docker named volume's
# mount point is created by the daemon (root, host-side) the first time it
# attaches at a path, which happens after the image's own build-time
# `chown -R appuser:appuser /var/lib/contract-agent` already ran - so an
# already-existing volume (this VM's real one, or any future fresh one)
# starts out root-owned regardless of what the image itself contains.
# Re-asserting ownership here, on every container start rather than only at
# image build time, self-heals both cases.
set -e

CHAT_ATTACHMENT_DIR="${CHAT_ATTACHMENT_STORAGE_DIR:-/var/lib/contract-agent/chat-attachments}"
mkdir -p "$CHAT_ATTACHMENT_DIR"
chown -R appuser:appuser "$CHAT_ATTACHMENT_DIR"

exec gosu appuser "$@"
