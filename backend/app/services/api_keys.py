"""Хранилище ключей интеграций. Открытый ключ возвращается один раз."""

import hashlib
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


class ApiKeyInfo(BaseModel):
    id: str
    name: str
    created_at: datetime
    revoked_at: datetime | None


class CreatedApiKey(ApiKeyInfo):
    key: str


class ApiKeyStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        digest TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        revoked_at TEXT
                    )
                """)
                yield connection
        finally:
            connection.close()

    def list(self) -> list[ApiKeyInfo]:
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT id, name, created_at, revoked_at FROM api_keys ORDER BY created_at DESC
            """).fetchall()
        return [ApiKeyInfo.model_validate(dict(row)) for row in rows]

    def create(self, name: str) -> CreatedApiKey:
        key = "drk_" + secrets.token_urlsafe(32)
        key_id = secrets.token_hex(16)
        now = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO api_keys (id, name, digest, created_at) VALUES (?, ?, ?, ?)",
                (key_id, name, hashlib.sha256(key.encode()).hexdigest(), now.isoformat()),
            )
        return CreatedApiKey(id=key_id, name=name, created_at=now, revoked_at=None, key=key)

    def resolve(self, key: str) -> str | None:
        if len(key) != 47 or not key.startswith("drk_"):
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM api_keys WHERE digest = ? AND revoked_at IS NULL", (digest,)
            ).fetchone()
        return row["id"] if row else None

    def revoke(self, key_id: str) -> bool:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
                (datetime.now(UTC).isoformat(), key_id),
            )
        return bool(updated.rowcount)
