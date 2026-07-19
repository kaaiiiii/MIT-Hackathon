from __future__ import annotations

import json
from typing import Any

from ..caller.errors import ExternalServiceError, NotFoundError, ValidationError
from .openai_researcher import ContextResearcher
from .persistence import SQLiteResearchStore
from .schemas import (
    ReportContext,
    ResearchBundle,
    ResearchStage,
)


class ResearchService:
    def __init__(
        self,
        *,
        store: SQLiteResearchStore,
        researcher: ContextResearcher | None,
        estimator_service: Any,
    ) -> None:
        self.store = store
        self.researcher = researcher
        self.estimator_service = estimator_service

    def _require_researcher(self) -> ContextResearcher:
        if self.researcher is None:
            raise ExternalServiceError(
                "GPT research is not configured. Set OPENAI_API_KEY and restart the API."
            )
        return self.researcher

    async def enrich_intake(self, session_id: str) -> ResearchBundle:
        researcher = self._require_researcher()
        view = self.estimator_service.get_session(session_id)
        if not view.fields:
            raise ValidationError(
                "Capture at least one evidenced intake field before starting research"
            )
        payload = {
            "stage": ResearchStage.ESTIMATOR_ENRICHMENT.value,
            "vertical": view.vertical,
            "schema_version": view.schema_version,
            "confirmed_candidate_fields": {
                name: field.model_dump(mode="json")
                for name, field in view.fields.items()
            },
            "unresolved_or_missing_fields": view.missing_required_fields,
            "voice_transcript": self.store.voice_turns(session_id),
            "instruction": (
                "Extend context around this job as deeply as reliable sources allow. "
                "Keep possible facts and questions separate from evidenced job facts."
            ),
        }
        result = await researcher.research(
            stage=ResearchStage.ESTIMATOR_ENRICHMENT.value,
            payload=payload,
        )
        return self.store.save(
            stage=ResearchStage.ESTIMATOR_ENRICHMENT,
            intake_session_id=session_id,
            model=researcher.model_name,
            artifact=result.artifact,
            provider_response_id=result.response_id,
        )

    async def final_research(
        self, version_id: str, call_ids: list[str] | None = None
    ) -> ResearchBundle:
        researcher = self._require_researcher()
        spec = self._confirmed_spec(version_id)
        all_calls = self.completed_calls(version_id, include_nonterminal=True)
        if call_ids is not None:
            requested = set(call_ids)
            unknown = requested - {item["call_id"] for item in all_calls}
            if unknown:
                raise ValidationError(
                    "Calls do not belong to this confirmed specification: "
                    + ", ".join(sorted(unknown))
                )
            selected = [item for item in all_calls if item["call_id"] in requested]
        else:
            selected = all_calls
        if not selected:
            raise ValidationError("Final research requires at least one completed call")
        unfinished = [item["call_id"] for item in selected if not item["terminal"]]
        if unfinished:
            raise ValidationError(
                "Final research can run only after all selected calls are terminal: "
                + ", ".join(unfinished)
            )
        initial = self.store.latest_for_spec(
            version_id, ResearchStage.ESTIMATOR_ENRICHMENT
        )
        payload = {
            "stage": ResearchStage.FINAL_REPORT_RESEARCH.value,
            "confirmed_job_spec": spec,
            "estimator_research": (
                initial.artifact.model_dump(mode="json") if initial else None
            ),
            "completed_call_records": selected,
            "instruction": (
                "Perform a fresh, current, deep web research pass for report context. "
                "Do not rank vendors or turn contextual research into quote evidence."
            ),
        }
        result = await researcher.research(
            stage=ResearchStage.FINAL_REPORT_RESEARCH.value,
            payload=payload,
        )
        ids = [item["call_id"] for item in selected]
        return self.store.save(
            stage=ResearchStage.FINAL_REPORT_RESEARCH,
            job_spec_version_id=version_id,
            based_on_call_ids=ids,
            model=researcher.model_name,
            artifact=result.artifact,
            provider_response_id=result.response_id,
        )

    def report_context(self, version_id: str) -> ReportContext:
        return ReportContext(
            job_spec_version_id=version_id,
            confirmed_job_spec=self._confirmed_spec(version_id),
            estimator_research=self.store.latest_for_spec(
                version_id, ResearchStage.ESTIMATOR_ENRICHMENT
            ),
            completed_calls=self.completed_calls(version_id),
            final_research=self.store.latest_for_spec(
                version_id, ResearchStage.FINAL_REPORT_RESEARCH
            ),
        )

    def _confirmed_spec(self, version_id: str) -> dict:
        row = self.store.connection.execute(
            """
            SELECT j.version_id, j.status, j.facts_json, j.confirmed_at,
                   e.canonical_hash
            FROM job_spec_versions j
            LEFT JOIN estimator_confirmed_specs e ON e.version_id = j.version_id
            WHERE j.version_id = ? AND j.status = 'confirmed'
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Confirmed specification {version_id!r} was not found")
        return {
            "version_id": row["version_id"],
            "status": row["status"],
            "facts": json.loads(row["facts_json"]),
            "confirmed_at": row["confirmed_at"],
            "canonical_hash": row["canonical_hash"],
        }

    def completed_calls(
        self,
        version_id: str,
        *,
        include_nonterminal: bool = False,
        exclude_call_id: str | None = None,
    ) -> list[dict]:
        rows = self.store.connection.execute(
            """
            SELECT c.*, o.outcome_type, o.reason, o.validation_warnings_json
            FROM calls c
            LEFT JOIN call_outcomes o ON o.call_id = c.call_id
            WHERE c.job_spec_version_id = ?
            ORDER BY c.created_at, c.call_id
            """,
            (version_id,),
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            if row["call_id"] == exclude_call_id:
                continue
            terminal = row["outcome_type"] is not None or row["status"] == "failed"
            if not terminal and not include_nonterminal:
                continue
            transcript = [
                dict(item)
                for item in self.store.connection.execute(
                    """
                    SELECT event_id, speaker, text, timestamp_seconds, sequence
                    FROM transcript_events WHERE call_id = ? ORDER BY sequence
                    """,
                    (row["call_id"],),
                ).fetchall()
            ]
            line_items = [
                dict(item)
                for item in self.store.connection.execute(
                    """
                    SELECT li.line_item_id, li.category, li.description, li.amount,
                           li.quantity, li.unit, li.unit_price, li.currency,
                           ev.transcript_event_id, ev.timestamp_seconds
                    FROM quote_versions qv
                    JOIN quote_line_items li
                      ON li.quote_version_id = qv.quote_version_id
                    LEFT JOIN quote_evidence ev
                      ON ev.claim_type = 'line_item'
                     AND ev.claim_id = li.line_item_id
                    WHERE qv.call_id = ? ORDER BY li.rowid
                    """,
                    (row["call_id"],),
                ).fetchall()
            ]
            terms = []
            for item in self.store.connection.execute(
                """
                SELECT qt.term_id, qt.category, qt.key, qt.value_json,
                       ev.transcript_event_id, ev.timestamp_seconds
                FROM quote_versions qv
                JOIN quote_terms qt ON qt.quote_version_id = qv.quote_version_id
                LEFT JOIN quote_evidence ev
                  ON ev.claim_type = 'term' AND ev.claim_id = qt.term_id
                WHERE qv.call_id = ? ORDER BY qt.rowid
                """,
                (row["call_id"],),
            ).fetchall():
                value = dict(item)
                value["value"] = json.loads(value.pop("value_json"))
                terms.append(value)
            result.append(
                {
                    "call_id": row["call_id"],
                    "vendor_id": row["vendor_id"],
                    "status": row["status"],
                    "terminal": terminal,
                    "outcome_type": row["outcome_type"],
                    "outcome_reason": row["reason"],
                    "validation_warnings": (
                        json.loads(row["validation_warnings_json"])
                        if row["validation_warnings_json"]
                        else []
                    ),
                    "line_items": line_items,
                    "terms": terms,
                    "transcript": transcript,
                    "recording_reference": row["recording_id"],
                }
            )
        return result


class CallerResearchContextProvider:
    """Builds and persists the exact non-authoritative context used by a call."""

    def __init__(self, service: ResearchService) -> None:
        self.service = service

    def build_and_snapshot(self, call_id: str, version_id: str) -> dict:
        initial = self.service.store.latest_for_spec(
            version_id, ResearchStage.ESTIMATOR_ENRICHMENT
        )
        prior_calls = self.service.completed_calls(
            version_id, exclude_call_id=call_id
        )[-8:]
        context = {
            "estimator_research": (
                initial.model_dump(mode="json") if initial else None
            ),
            "prior_completed_calls": prior_calls,
            "usage_boundary": (
                "Use this context to choose more accurate questions. It is not a "
                "confirmed job fact and is not authorized competing-bid leverage."
            ),
        }
        self.service.store.save_call_context(call_id, context)
        return context

    def get_snapshot(self, call_id: str) -> dict | None:
        return self.service.store.get_call_context(call_id)
