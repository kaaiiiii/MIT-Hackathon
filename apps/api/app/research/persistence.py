from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from .schemas import ResearchArtifact, ResearchBundle, ResearchStage


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_bundles (
    research_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    intake_session_id TEXT,
    job_spec_version_id TEXT,
    based_on_call_ids_json TEXT NOT NULL,
    model TEXT NOT NULL,
    artifact_json TEXT NOT NULL,
    provider_response_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_context_snapshots (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id),
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_research_session_stage
ON research_bundles(intake_session_id, stage, created_at);
CREATE INDEX IF NOT EXISTS idx_research_spec_stage
ON research_bundles(job_spec_version_id, stage, created_at);
"""


class SQLiteResearchStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self.connection.executescript(SCHEMA_SQL)

    def save(
        self,
        *,
        stage: ResearchStage,
        model: str,
        artifact: ResearchArtifact,
        intake_session_id: str | None = None,
        job_spec_version_id: str | None = None,
        based_on_call_ids: list[str] | None = None,
        provider_response_id: str | None = None,
    ) -> ResearchBundle:
        bundle = ResearchBundle(
            research_id=f"research_{uuid4().hex}",
            stage=stage,
            intake_session_id=intake_session_id,
            job_spec_version_id=job_spec_version_id,
            based_on_call_ids=based_on_call_ids or [],
            model=model,
            artifact=artifact,
            provider_response_id=provider_response_id,
            created_at=datetime.now(UTC),
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO research_bundles (
                    research_id, stage, intake_session_id, job_spec_version_id,
                    based_on_call_ids_json, model, artifact_json,
                    provider_response_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle.research_id,
                    bundle.stage.value,
                    bundle.intake_session_id,
                    bundle.job_spec_version_id,
                    json.dumps(bundle.based_on_call_ids),
                    bundle.model,
                    bundle.artifact.model_dump_json(),
                    bundle.provider_response_id,
                    bundle.created_at.isoformat(),
                ),
            )
        return bundle

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> ResearchBundle | None:
        if row is None:
            return None
        return ResearchBundle(
            research_id=row["research_id"],
            stage=row["stage"],
            intake_session_id=row["intake_session_id"],
            job_spec_version_id=row["job_spec_version_id"],
            based_on_call_ids=json.loads(row["based_on_call_ids_json"]),
            model=row["model"],
            artifact=ResearchArtifact.model_validate_json(row["artifact_json"]),
            provider_response_id=row["provider_response_id"],
            created_at=row["created_at"],
        )

    def latest_for_session(
        self, session_id: str, stage: ResearchStage
    ) -> ResearchBundle | None:
        row = self.connection.execute(
            """
            SELECT * FROM research_bundles
            WHERE intake_session_id = ? AND stage = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id, stage.value),
        ).fetchone()
        return self._from_row(row)

    def latest_for_spec(
        self, version_id: str, stage: ResearchStage
    ) -> ResearchBundle | None:
        if stage == ResearchStage.ESTIMATOR_ENRICHMENT:
            row = self.connection.execute(
                """
                SELECT rb.* FROM research_bundles rb
                LEFT JOIN estimator_confirmed_specs ecs
                  ON ecs.source_session_id = rb.intake_session_id
                WHERE rb.stage = ?
                  AND (rb.job_spec_version_id = ? OR ecs.version_id = ?)
                ORDER BY rb.created_at DESC LIMIT 1
                """,
                (stage.value, version_id, version_id),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT * FROM research_bundles
                WHERE job_spec_version_id = ? AND stage = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (version_id, stage.value),
            ).fetchone()
        return self._from_row(row)

    def voice_turns(self, session_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT turn_id, user_text, captured_at FROM intake_voice_turns
            WHERE session_id = ? ORDER BY captured_at, rowid
            """,
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_call_context(self, call_id: str, context: dict) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO call_context_snapshots(call_id, context_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    context_json = excluded.context_json,
                    created_at = excluded.created_at
                """,
                (
                    call_id,
                    json.dumps(context, sort_keys=True, separators=(",", ":")),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_call_context(self, call_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT context_json FROM call_context_snapshots WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        return json.loads(row["context_json"]) if row else None
