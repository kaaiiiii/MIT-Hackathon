from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from .errors import (
    EstimatorConflictError,
    EstimatorNotFoundError,
    EstimatorValidationError,
)
from .schemas import (
    BenchmarkRef,
    CatalogCandidate,
    EvidencedField,
    IntakeStatus,
    PendingCatalogResolution,
)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS intake_sessions (
    session_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE,
    vertical TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL,
    base_version_id TEXT,
    benchmark_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_voice_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(session_id),
    user_text TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_field_evidence (
    evidence_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(session_id),
    field_name TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    review_required INTEGER NOT NULL DEFAULT 0,
    conflict_resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_question_attempts (
    session_id TEXT NOT NULL REFERENCES intake_sessions(session_id),
    field_name TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    PRIMARY KEY(session_id, field_name)
);

CREATE TABLE IF NOT EXISTS intake_catalog_resolutions (
    resolution_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES intake_sessions(session_id),
    field_name TEXT NOT NULL,
    raw_statement TEXT NOT NULL,
    catalog_name TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    selected_candidate_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS estimator_confirmed_specs (
    version_id TEXT PRIMARY KEY,
    vertical TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    canonical_hash TEXT NOT NULL,
    source_session_id TEXT NOT NULL UNIQUE,
    superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS job_spec_versions (
    version_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_intake_evidence_session
ON intake_field_evidence(session_id, field_name, selected);
CREATE INDEX IF NOT EXISTS idx_intake_catalog_session
ON intake_catalog_resolutions(session_id, status);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class SQLiteEstimatorStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self.connection.executescript(SCHEMA_SQL)
            columns = {
                row[1]
                for row in self.connection.execute(
                    "PRAGMA table_info(intake_field_evidence)"
                ).fetchall()
            }
            if "conflict_resolved" not in columns:
                self.connection.execute(
                    "ALTER TABLE intake_field_evidence "
                    "ADD COLUMN conflict_resolved INTEGER NOT NULL DEFAULT 0"
                )

    def create_session(
        self,
        *,
        vertical: str,
        schema_version: str,
        base_version_id: str | None,
        benchmark_refs: list[BenchmarkRef],
    ) -> str:
        session_id = f"intake_{uuid4().hex}"
        now = _now().isoformat()
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO intake_sessions (
                    session_id, draft_id, vertical, schema_version, status,
                    base_version_id, benchmark_refs_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    f"draft_{uuid4().hex}",
                    vertical,
                    schema_version,
                    IntakeStatus.DRAFT.value,
                    base_version_id,
                    _dump([item.model_dump(mode="json") for item in benchmark_refs]),
                    now,
                    now,
                ),
            )
        return session_id

    def get_session_row(self, session_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM intake_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise EstimatorNotFoundError(f"Intake session {session_id!r} was not found")
        return row

    def update_status(self, session_id: str, status: IntakeStatus) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                "UPDATE intake_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status.value, _now().isoformat(), session_id),
            )

    def add_voice_turn(self, session_id: str, turn_id: str, text: str) -> datetime:
        captured = _now()
        try:
            with self._lock, self.connection:
                self.connection.execute(
                    """
                    INSERT INTO intake_voice_turns
                        (turn_id, session_id, user_text, captured_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (turn_id, session_id, text, captured.isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise EstimatorConflictError("Voice turn IDs must be unique") from exc
        return captured

    def voice_turn_exists(self, session_id: str, turn_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM intake_voice_turns WHERE session_id = ? AND turn_id = ?",
            (session_id, turn_id),
        ).fetchone() is not None

    def add_evidence(
        self,
        session_id: str,
        field_name: str,
        evidence: EvidencedField,
        *,
        review_required: bool = False,
    ) -> str:
        self._assert_mutable(session_id)
        evidence_id = f"ev_{uuid4().hex}"
        current = self.selected_evidence(session_id).get(field_name)
        selected = int(current is None and not review_required)
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO intake_field_evidence (
                    evidence_id, session_id, field_name, evidence_json,
                    selected, review_required, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    session_id,
                    field_name,
                    evidence.model_dump_json(),
                    selected,
                    int(review_required),
                    _now().isoformat(),
                ),
            )
            self.connection.execute(
                "UPDATE intake_sessions SET updated_at = ? WHERE session_id = ?",
                (_now().isoformat(), session_id),
            )
        return evidence_id

    def evidence_candidates(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        rows = self.connection.execute(
            """
            SELECT evidence_id, field_name, evidence_json, selected, review_required,
                   conflict_resolved
            FROM intake_field_evidence WHERE session_id = ? ORDER BY created_at
            """,
            (session_id,),
        ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            result.setdefault(row["field_name"], []).append(
                {
                    "evidence_id": row["evidence_id"],
                    "evidence": json.loads(row["evidence_json"]),
                    "selected": bool(row["selected"]),
                    "review_required": bool(row["review_required"]),
                    "conflict_resolved": bool(row["conflict_resolved"]),
                }
            )
        return result

    def selected_evidence(self, session_id: str) -> dict[str, EvidencedField]:
        rows = self.connection.execute(
            """
            SELECT field_name, evidence_json FROM intake_field_evidence
            WHERE session_id = ? AND selected = 1
            """,
            (session_id,),
        ).fetchall()
        return {
            row["field_name"]: EvidencedField.model_validate_json(row["evidence_json"])
            for row in rows
        }

    def unresolved_conflicts(self, session_id: str) -> list[str]:
        candidates = self.evidence_candidates(session_id)
        unresolved = []
        for field_name, items in candidates.items():
            selected = [item for item in items if item["selected"]]
            values = {_dump(item["evidence"]["value"]) for item in items}
            if not selected or len(values) > 1 and any(
                not item["conflict_resolved"] for item in items
            ):
                unresolved.append(field_name)
        return sorted(unresolved)

    def select_evidence(self, session_id: str, field_name: str, evidence_id: str) -> None:
        row = self.connection.execute(
            """
            SELECT 1 FROM intake_field_evidence
            WHERE session_id = ? AND field_name = ? AND evidence_id = ?
            """,
            (session_id, field_name, evidence_id),
        ).fetchone()
        if row is None:
            raise EstimatorValidationError(
                f"Evidence {evidence_id!r} is not a candidate for {field_name!r}"
            )
        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE intake_field_evidence SET selected = 0
                WHERE session_id = ? AND field_name = ?
                """,
                (session_id, field_name),
            )
            self.connection.execute(
                """
                UPDATE intake_field_evidence
                SET selected = 1, review_required = 0
                WHERE evidence_id = ?
                """,
                (evidence_id,),
            )
            self.connection.execute(
                """
                UPDATE intake_field_evidence SET conflict_resolved = 1
                WHERE session_id = ? AND field_name = ?
                """,
                (session_id, field_name),
            )

    def increment_question_attempt(self, session_id: str, field_name: str) -> int:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO intake_question_attempts(session_id, field_name, attempts)
                VALUES (?, ?, 1)
                ON CONFLICT(session_id, field_name)
                DO UPDATE SET attempts = attempts + 1
                """,
                (session_id, field_name),
            )
        return self.question_attempts(session_id).get(field_name, 0)

    def question_attempts(self, session_id: str) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT field_name, attempts FROM intake_question_attempts WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return {row["field_name"]: row["attempts"] for row in rows}

    def create_resolution(
        self,
        session_id: str,
        field_name: str,
        raw_statement: str,
        catalog_name: str,
        candidates: list[CatalogCandidate],
    ) -> PendingCatalogResolution:
        self._assert_mutable(session_id)
        resolution = PendingCatalogResolution(
            resolution_id=f"res_{uuid4().hex}",
            field_name=field_name,
            raw_user_statement=raw_statement,
            catalog_queried=catalog_name,
            candidates=candidates,
            status="pending" if candidates else "no_candidates",
            created_at=_now(),
        )
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO intake_catalog_resolutions (
                    resolution_id, session_id, field_name, raw_statement,
                    catalog_name, candidates_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolution.resolution_id,
                    session_id,
                    field_name,
                    raw_statement,
                    catalog_name,
                    _dump([item.model_dump(mode="json") for item in candidates]),
                    resolution.status,
                    resolution.created_at.isoformat(),
                ),
            )
        return resolution

    def get_resolution(self, session_id: str, resolution_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            """
            SELECT * FROM intake_catalog_resolutions
            WHERE session_id = ? AND resolution_id = ?
            """,
            (session_id, resolution_id),
        ).fetchone()
        if row is None:
            raise EstimatorNotFoundError(
                f"Catalog resolution {resolution_id!r} was not found"
            )
        return row

    def complete_resolution(
        self, session_id: str, resolution_id: str, selected_candidate_id: str
    ) -> None:
        row = self.get_resolution(session_id, resolution_id)
        if row["status"] != "pending":
            raise EstimatorConflictError("Catalog resolution is not awaiting selection")
        candidates = [
            CatalogCandidate.model_validate(item)
            for item in json.loads(row["candidates_json"])
        ]
        if selected_candidate_id not in {item.candidate_id for item in candidates}:
            raise EstimatorValidationError("Selected candidate was not offered")
        with self._lock, self.connection:
            self.connection.execute(
                """
                UPDATE intake_catalog_resolutions
                SET status = 'resolved', selected_candidate_id = ?, resolved_at = ?
                WHERE resolution_id = ?
                """,
                (selected_candidate_id, _now().isoformat(), resolution_id),
            )

    def pending_resolutions(self, session_id: str) -> list[PendingCatalogResolution]:
        rows = self.connection.execute(
            """
            SELECT * FROM intake_catalog_resolutions
            WHERE session_id = ? AND status IN ('pending', 'no_candidates')
            ORDER BY created_at
            """,
            (session_id,),
        ).fetchall()
        return [
            PendingCatalogResolution(
                resolution_id=row["resolution_id"],
                field_name=row["field_name"],
                raw_user_statement=row["raw_statement"],
                catalog_queried=row["catalog_name"],
                candidates=[
                    CatalogCandidate.model_validate(item)
                    for item in json.loads(row["candidates_json"])
                ],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def benchmark_refs(self, session_id: str) -> list[BenchmarkRef]:
        row = self.get_session_row(session_id)
        return [
            BenchmarkRef.model_validate(item)
            for item in json.loads(row["benchmark_refs_json"])
        ]

    def save_confirmed(
        self,
        *,
        session_id: str,
        version_id: str,
        facts: dict[str, Any],
        confirmed_at: datetime,
        confirmed_by: str,
        canonical_hash: str,
    ) -> None:
        row = self.get_session_row(session_id)
        if row["status"] != IntakeStatus.AWAITING_CONFIRMATION.value:
            raise EstimatorValidationError(
                "Only an awaiting-confirmation draft can be confirmed"
            )
        facts_json = _dump(facts)
        try:
            with self._lock, self.connection:
                self.connection.execute(
                    """
                    INSERT INTO estimator_confirmed_specs (
                        version_id, vertical, schema_version, status, facts_json,
                        confirmed_at, confirmed_by, canonical_hash, source_session_id
                    ) VALUES (?, ?, ?, 'confirmed', ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        row["vertical"],
                        row["schema_version"],
                        facts_json,
                        confirmed_at.isoformat(),
                        confirmed_by,
                        canonical_hash,
                        session_id,
                    ),
                )
                # This is the sole Estimator write path into the Caller handoff table.
                self.connection.execute(
                    """
                    INSERT INTO job_spec_versions
                        (version_id, status, facts_json, confirmed_at)
                    VALUES (?, 'confirmed', ?, ?)
                    """,
                    (version_id, facts_json, confirmed_at.isoformat()),
                )
                self.connection.execute(
                    """
                    UPDATE intake_sessions SET status = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (IntakeStatus.CONFIRMED.value, _now().isoformat(), session_id),
                )
                if row["base_version_id"]:
                    self.connection.execute(
                        """
                        UPDATE estimator_confirmed_specs
                        SET status = 'superseded', superseded_by = ?
                        WHERE version_id = ?
                        """,
                        (version_id, row["base_version_id"]),
                    )
        except sqlite3.IntegrityError as exc:
            raise EstimatorConflictError("Confirmed version already exists") from exc

    def get_confirmed_row(self, version_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM estimator_confirmed_specs WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise EstimatorNotFoundError(f"Spec version {version_id!r} was not found")
        return row

    def shared_version_count(self) -> int:
        return int(
            self.connection.execute("SELECT COUNT(*) FROM job_spec_versions").fetchone()[0]
        )

    def _assert_mutable(self, session_id: str) -> None:
        status = IntakeStatus(self.get_session_row(session_id)["status"])
        if status in {IntakeStatus.CONFIRMED, IntakeStatus.SUPERSEDED}:
            raise EstimatorConflictError("Confirmed intake sessions are immutable")
