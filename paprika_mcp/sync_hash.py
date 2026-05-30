from __future__ import annotations

import hashlib
import uuid


def generate_sync_hash() -> str:
    """Return a Paprika-compatible local change token."""
    value = str(uuid.uuid4()).upper()
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()
