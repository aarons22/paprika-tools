from __future__ import annotations

import re

from paprika_mcp.sync_hash import generate_sync_hash


def test_generate_sync_hash_returns_uppercase_sha256_hex() -> None:
    sync_hash = generate_sync_hash()

    assert re.fullmatch(r"[0-9A-F]{64}", sync_hash)


def test_generate_sync_hash_is_non_deterministic() -> None:
    assert generate_sync_hash() != generate_sync_hash()
