"""SQLite persistence layer for incidents.

Uses the standard library ``sqlite3`` module only, so the app has no
external database dependency. Each environment (staging/production) points
at its own database file via configuration, keeping their data isolated.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentStore:
    """Thin repository wrapper around a SQLite database file."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        finally:
            conn.close()

    def ping(self) -> bool:
        """Used by the /health endpoint to confirm the DB is reachable."""
        with self._cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() is not None

    def create(self, title: str, description: str, severity: str, status: str) -> dict:
        ts = now_iso()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO incidents
                   (title, description, severity, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (title, description, severity, status, ts, ts),
            )
            new_id = cur.lastrowid
        return self.get(new_id)

    def get(self, incident_id: int) -> Optional[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def list(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[dict]:
        query = "SELECT * FROM incidents WHERE 1=1"
        params: list = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if q:
            query += " AND (title LIKE ? OR description LIKE ?)"
            like = f"%{q}%"
            params.extend([like, like])
        query += " ORDER BY created_at DESC"
        with self._cursor() as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    # Fields update() is allowed to set. Bandit's B608 check flags the
    # dynamic "SET column = ?" string below on principle, because it
    # cannot see that the column names come only from this fixed
    # allow-list rather than from raw request data. The suppression
    # directive on that line is explained in the Security section of
    # README.md, not applied silently.
    _UPDATABLE_FIELDS = frozenset({"title", "description", "severity", "status", "updated_at"})

    def update(self, incident_id: int, fields: dict) -> Optional[dict]:
        if not fields:
            return self.get(incident_id)
        fields = dict(fields)
        fields["updated_at"] = now_iso()
        unknown = set(fields) - self._UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"cannot update unknown field(s): {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [incident_id]
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE incidents SET {assignments} WHERE id = ?", params  # nosec B608
            )
            if cur.rowcount == 0:
                return None
        return self.get(incident_id)

    def delete(self, incident_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
            return cur.rowcount > 0

    # Static, fully-formed queries per allowed column -- no dynamic SQL
    # string construction, so there is nothing for Bandit's B608 check to
    # flag here (unlike update() above).
    _COUNTABLE_COLUMNS = {
        "status": "SELECT status AS k, COUNT(*) AS c FROM incidents GROUP BY status",
        "severity": "SELECT severity AS k, COUNT(*) AS c FROM incidents GROUP BY severity",
    }

    def counts_by(self, column: str) -> dict:
        if column not in self._COUNTABLE_COLUMNS:
            raise ValueError(f"cannot count by unknown column: {column}")
        with self._cursor() as cur:
            cur.execute(self._COUNTABLE_COLUMNS[column])
            return {row["k"]: row["c"] for row in cur.fetchall()}
