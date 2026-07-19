from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sample_sessions (
    session_id TEXT PRIMARY KEY,
    target_quotes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sample_recordings (
    recording_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sample_sessions(session_id),
    source TEXT NOT NULL CHECK (source IN ('recorded', 'synthetic')),
    agency_name TEXT,
    transcript TEXT NOT NULL,
    quote_total REAL,
    currency TEXT NOT NULL DEFAULT 'USD',
    audio_path TEXT,
    media_type TEXT,
    created_at TEXT NOT NULL
);
"""


class SQLiteSamplesStore:
    """Owns the sample-call tables on the shared caller connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.executescript(_SCHEMA)
        self.connection.commit()

    def create_session(self, session_id: str, target_quotes: int) -> None:
        self.connection.execute(
            "INSERT INTO sample_sessions (session_id, target_quotes, created_at) "
            "VALUES (?, ?, ?)",
            (session_id, target_quotes, datetime.now(UTC).isoformat()),
        )
        self.connection.commit()

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sample_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

    def add_recording(
        self,
        *,
        recording_id: str,
        session_id: str,
        source: str,
        agency_name: str | None,
        transcript: str,
        quote_total: float | None,
        currency: str = "USD",
        audio_path: str | None = None,
        media_type: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO sample_recordings (recording_id, session_id, source, "
            "agency_name, transcript, quote_total, currency, audio_path, "
            "media_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recording_id,
                session_id,
                source,
                agency_name,
                transcript,
                quote_total,
                currency,
                audio_path,
                media_type,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.connection.commit()

    def recordings(self, session_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM sample_recordings WHERE session_id = ? "
            "ORDER BY created_at, recording_id",
            (session_id,),
        ).fetchall()

    def get_recording(
        self, session_id: str, recording_id: str
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM sample_recordings WHERE session_id = ? AND recording_id = ?",
            (session_id, recording_id),
        ).fetchone()
