from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .errors import ConflictError, NotFoundError
from .schemas import (
    AgentConfiguration,
    CallPolicy,
    CallRecord,
    EvidenceInput,
    OutcomeType,
    QuoteDraft,
    QuoteLineItem,
    QuoteLineItemInput,
    QuoteTerm,
    QuoteTermInput,
    StructuredCallOutcome,
    TranscriptEvent,
    TranscriptEventInput,
)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    job_spec_version_id TEXT NOT NULL,
    spec_sha256 TEXT NOT NULL,
    call_type TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_session_id TEXT,
    started_at TEXT,
    ended_at TEXT,
    transcript_id TEXT NOT NULL UNIQUE,
    recording_id TEXT,
    final_outcome TEXT,
    policy_json TEXT NOT NULL,
    agent_configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_events (
    event_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcript_events (
    event_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    speaker TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp_seconds REAL NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(call_id, sequence)
);

CREATE TABLE IF NOT EXISTS quote_versions (
    quote_version_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finalized_at TEXT,
    UNIQUE(call_id, version)
);

CREATE TABLE IF NOT EXISTS quote_line_items (
    line_item_id TEXT PRIMARY KEY,
    quote_version_id TEXT NOT NULL REFERENCES quote_versions(quote_version_id),
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL,
    quantity REAL,
    unit TEXT,
    unit_price REAL,
    currency TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_terms (
    term_id TEXT PRIMARY KEY,
    quote_version_id TEXT NOT NULL REFERENCES quote_versions(quote_version_id),
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quote_evidence (
    evidence_id TEXT PRIMARY KEY,
    quote_version_id TEXT NOT NULL REFERENCES quote_versions(quote_version_id),
    claim_type TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    transcript_event_id TEXT NOT NULL REFERENCES transcript_events(event_id),
    timestamp_seconds REAL NOT NULL,
    confidence TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(claim_type, claim_id)
);

CREATE TABLE IF NOT EXISTS call_outcomes (
    outcome_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE REFERENCES calls(call_id),
    outcome_type TEXT NOT NULL,
    quote_version_id TEXT REFERENCES quote_versions(quote_version_id),
    reason TEXT,
    validation_warnings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transcript_call ON transcript_events(call_id, sequence);
CREATE INDEX IF NOT EXISTS idx_call_events_call ON call_events(call_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quote_versions_call ON quote_versions(call_id, version);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


class SQLiteCallerStore:
    def __init__(self, database: str | Path = "caller.sqlite3") -> None:
        self.connection = sqlite3.connect(
            str(database), check_same_thread=False, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self.connection.executescript(SCHEMA_SQL)

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_call(self, call: CallRecord) -> None:
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO calls (
                    call_id, vendor_id, job_spec_version_id, spec_sha256, call_type,
                    status, provider_session_id, started_at, ended_at, transcript_id,
                    recording_id, final_outcome, policy_json,
                    agent_configuration_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call.call_id,
                    call.vendor_id,
                    call.job_spec_version_id,
                    call.spec_sha256,
                    call.call_type,
                    call.status.value,
                    call.provider_session_id,
                    call.started_at.isoformat() if call.started_at else None,
                    call.ended_at.isoformat() if call.ended_at else None,
                    call.transcript_id,
                    call.recording_id,
                    call.final_outcome.value if call.final_outcome else None,
                    call.policy.model_dump_json(),
                    call.agent_configuration.model_dump_json(),
                    call.created_at.isoformat(),
                ),
            )

    def _call_from_row(self, row: sqlite3.Row) -> CallRecord:
        return CallRecord(
            call_id=row["call_id"],
            vendor_id=row["vendor_id"],
            job_spec_version_id=row["job_spec_version_id"],
            spec_sha256=row["spec_sha256"],
            call_type=row["call_type"],
            status=row["status"],
            provider_session_id=row["provider_session_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            transcript_id=row["transcript_id"],
            recording_id=row["recording_id"],
            final_outcome=row["final_outcome"],
            policy=CallPolicy.model_validate_json(row["policy_json"]),
            agent_configuration=AgentConfiguration.model_validate_json(
                row["agent_configuration_json"]
            ),
            created_at=row["created_at"],
        )

    def get_call(self, call_id: str) -> CallRecord | None:
        row = self.connection.execute(
            "SELECT * FROM calls WHERE call_id = ?", (call_id,)
        ).fetchone()
        return self._call_from_row(row) if row else None

    def update_call(self, call: CallRecord) -> None:
        with self._transaction():
            cursor = self.connection.execute(
                """
                UPDATE calls SET status = ?, provider_session_id = ?, started_at = ?,
                    ended_at = ?, recording_id = ?, final_outcome = ?
                WHERE call_id = ?
                """,
                (
                    call.status.value,
                    call.provider_session_id,
                    call.started_at.isoformat() if call.started_at else None,
                    call.ended_at.isoformat() if call.ended_at else None,
                    call.recording_id,
                    call.final_outcome.value if call.final_outcome else None,
                    call.call_id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"Call {call.call_id!r} was not found")

    def append_call_event(self, call_id: str, event_type: str, payload: dict) -> str:
        event_id = f"ce_{uuid4().hex}"
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO call_events
                    (event_id, call_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, call_id, event_type, _json(payload), _now().isoformat()),
            )
        return event_id

    def append_transcript(
        self, call_id: str, event: TranscriptEventInput
    ) -> TranscriptEvent:
        result = TranscriptEvent(
            event_id=event.event_id or f"te_{uuid4().hex}",
            call_id=call_id,
            speaker=event.speaker,
            text=event.text,
            timestamp_seconds=event.timestamp_seconds,
            sequence=event.sequence,
            created_at=_now(),
        )
        try:
            with self._transaction():
                self.connection.execute(
                    """
                    INSERT INTO transcript_events
                        (event_id, call_id, speaker, text, timestamp_seconds,
                         sequence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.event_id,
                        result.call_id,
                        result.speaker,
                        result.text,
                        result.timestamp_seconds,
                        result.sequence,
                        result.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                "Transcript event id and sequence must be unique within a call"
            ) from exc
        return result

    def _transcript_from_row(self, row: sqlite3.Row) -> TranscriptEvent:
        return TranscriptEvent(
            event_id=row["event_id"],
            call_id=row["call_id"],
            speaker=row["speaker"],
            text=row["text"],
            timestamp_seconds=row["timestamp_seconds"],
            sequence=row["sequence"],
            created_at=row["created_at"],
        )

    def get_transcript_event(self, event_id: str) -> TranscriptEvent | None:
        row = self.connection.execute(
            "SELECT * FROM transcript_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._transcript_from_row(row) if row else None

    def list_transcript(self, call_id: str) -> list[TranscriptEvent]:
        rows = self.connection.execute(
            "SELECT * FROM transcript_events WHERE call_id = ? ORDER BY sequence",
            (call_id,),
        ).fetchall()
        return [self._transcript_from_row(row) for row in rows]

    def create_quote_draft(self, call_id: str) -> QuoteDraft:
        draft = QuoteDraft(
            quote_version_id=f"qv_{uuid4().hex}",
            call_id=call_id,
            version=1,
            status="draft",
        )
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO quote_versions
                    (quote_version_id, call_id, version, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    draft.quote_version_id,
                    call_id,
                    draft.version,
                    draft.status,
                    _now().isoformat(),
                ),
            )
        return draft

    def _quote_row(self, call_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            """
            SELECT * FROM quote_versions
            WHERE call_id = ? ORDER BY version DESC LIMIT 1
            """,
            (call_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Quote draft for call {call_id!r} was not found")
        return row

    def _evidence_for(self, claim_type: str, claim_id: str) -> EvidenceInput:
        row = self.connection.execute(
            """
            SELECT transcript_event_id, timestamp_seconds, confidence, source
            FROM quote_evidence WHERE claim_type = ? AND claim_id = ?
            """,
            (claim_type, claim_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Evidence for {claim_type} {claim_id!r} was not found")
        return EvidenceInput(**dict(row))

    def get_quote_draft(self, call_id: str) -> QuoteDraft:
        quote = self._quote_row(call_id)
        line_rows = self.connection.execute(
            "SELECT * FROM quote_line_items WHERE quote_version_id = ?",
            (quote["quote_version_id"],),
        ).fetchall()
        term_rows = self.connection.execute(
            "SELECT * FROM quote_terms WHERE quote_version_id = ?",
            (quote["quote_version_id"],),
        ).fetchall()
        line_items = [
            QuoteLineItem(
                line_item_id=row["line_item_id"],
                quote_version_id=row["quote_version_id"],
                category=row["category"],
                description=row["description"],
                amount=row["amount"],
                quantity=row["quantity"],
                unit=row["unit"],
                unit_price=row["unit_price"],
                currency=row["currency"],
                evidence=self._evidence_for("line_item", row["line_item_id"]),
            )
            for row in line_rows
        ]
        terms = [
            QuoteTerm(
                term_id=row["term_id"],
                quote_version_id=row["quote_version_id"],
                category=row["category"],
                key=row["key"],
                value=json.loads(row["value_json"]),
                evidence=self._evidence_for("term", row["term_id"]),
            )
            for row in term_rows
        ]
        return QuoteDraft(
            quote_version_id=quote["quote_version_id"],
            call_id=call_id,
            version=quote["version"],
            status=quote["status"],
            line_items=line_items,
            terms=terms,
        )

    def _save_evidence(
        self,
        quote_version_id: str,
        claim_type: str,
        claim_id: str,
        evidence: EvidenceInput,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO quote_evidence (
                evidence_id, quote_version_id, claim_type, claim_id,
                transcript_event_id, timestamp_seconds, confidence, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ev_{uuid4().hex}",
                quote_version_id,
                claim_type,
                claim_id,
                evidence.transcript_event_id,
                evidence.timestamp_seconds,
                evidence.confidence.value,
                evidence.source,
            ),
        )

    def add_line_item(
        self, call_id: str, item: QuoteLineItemInput
    ) -> QuoteLineItem:
        quote = self._quote_row(call_id)
        if quote["status"] != "draft":
            raise ConflictError("Cannot change a finalized quote")
        line_item_id = f"qli_{uuid4().hex}"
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO quote_line_items (
                    line_item_id, quote_version_id, category, description, amount,
                    quantity, unit, unit_price, currency
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line_item_id,
                    quote["quote_version_id"],
                    item.category,
                    item.description,
                    item.amount,
                    item.quantity,
                    item.unit,
                    item.unit_price,
                    item.currency,
                ),
            )
            self._save_evidence(
                quote["quote_version_id"], "line_item", line_item_id, item.evidence
            )
        return QuoteLineItem(
            line_item_id=line_item_id,
            quote_version_id=quote["quote_version_id"],
            **item.model_dump(),
        )

    def add_term(self, call_id: str, term: QuoteTermInput) -> QuoteTerm:
        quote = self._quote_row(call_id)
        if quote["status"] != "draft":
            raise ConflictError("Cannot change a finalized quote")
        term_id = f"qt_{uuid4().hex}"
        with self._transaction():
            self.connection.execute(
                """
                INSERT INTO quote_terms
                    (term_id, quote_version_id, category, key, value_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    term_id,
                    quote["quote_version_id"],
                    term.category,
                    term.key,
                    _json(term.value),
                ),
            )
            self._save_evidence(
                quote["quote_version_id"], "term", term_id, term.evidence
            )
        return QuoteTerm(
            term_id=term_id,
            quote_version_id=quote["quote_version_id"],
            **term.model_dump(),
        )

    def finalize_quote(self, call_id: str) -> QuoteDraft:
        quote = self._quote_row(call_id)
        with self._transaction():
            self.connection.execute(
                """
                UPDATE quote_versions SET status = 'final', finalized_at = ?
                WHERE quote_version_id = ?
                """,
                (_now().isoformat(), quote["quote_version_id"]),
            )
        return self.get_quote_draft(call_id)

    def save_outcome(self, outcome: StructuredCallOutcome) -> None:
        try:
            with self._transaction():
                self.connection.execute(
                    """
                    INSERT INTO call_outcomes (
                        outcome_id, call_id, outcome_type, quote_version_id, reason,
                        validation_warnings_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.outcome_id,
                        outcome.call_id,
                        outcome.outcome_type.value,
                        outcome.quote_version_id,
                        outcome.reason,
                        _json(outcome.validation_warnings),
                        outcome.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("A final outcome already exists for this call") from exc

    def get_outcome(self, call_id: str) -> StructuredCallOutcome | None:
        row = self.connection.execute(
            "SELECT * FROM call_outcomes WHERE call_id = ?", (call_id,)
        ).fetchone()
        if row is None:
            return None
        return StructuredCallOutcome(
            outcome_id=row["outcome_id"],
            call_id=row["call_id"],
            outcome_type=OutcomeType(row["outcome_type"]),
            quote_version_id=row["quote_version_id"],
            reason=row["reason"],
            validation_warnings=json.loads(row["validation_warnings_json"]),
            created_at=row["created_at"],
        )

