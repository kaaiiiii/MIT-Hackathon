from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .errors import EstimatorConflictError, EstimatorValidationError
from .persistence import SQLiteEstimatorStore
from .planner import IntakeQuestionPlanner
from .ports import CatalogResolver, DocumentParser, IntakeVoiceAdapter
from .schemas import (
    BenchmarkRef,
    CatalogCandidate,
    CatalogResolution,
    ConfirmSpecRequest,
    ConfirmedJobSpecView,
    DocumentParseResult,
    EvidenceModality,
    EvidenceSource,
    EvidencedField,
    FieldConfidence,
    IntakeSessionCreate,
    IntakeSessionView,
    IntakeStatus,
    VoiceTurnRequest,
    VoiceTurnResponse,
)
from .verticals import VerticalConfig, VerticalConfigLoader


AI_DISCLOSURE = "I'm an AI assistant helping you build a job specification for quotes."


class EstimatorService:
    def __init__(
        self,
        *,
        store: SQLiteEstimatorStore,
        configs: VerticalConfigLoader,
        document_parser: DocumentParser,
        catalog_resolver: CatalogResolver,
        voice_adapter: IntakeVoiceAdapter,
    ) -> None:
        self.store = store
        self.configs = configs
        self.document_parser = document_parser
        self.catalog_resolver = catalog_resolver
        self.voice_adapter = voice_adapter
        self.questions = IntakeQuestionPlanner()

    def create_session(self, request: IntakeSessionCreate) -> IntakeSessionView:
        config = self.configs.load(request.vertical)
        self._validate_benchmark_refs(config, request.benchmark_refs)
        base_fields: dict[str, EvidencedField] = {}
        benchmark_refs = request.benchmark_refs
        if request.base_version_id:
            base = self.store.get_confirmed_row(request.base_version_id)
            if base["vertical"] != request.vertical:
                raise EstimatorValidationError(
                    "A new draft must use the same vertical as its base version"
                )
            facts = json.loads(base["facts_json"])
            base_fields = {
                name: EvidencedField.model_validate(value)
                for name, value in facts["fields"].items()
            }
            if not benchmark_refs:
                benchmark_refs = [
                    BenchmarkRef.model_validate(item)
                    for item in facts.get("benchmark_refs", [])
                ]
        session_id = self.store.create_session(
            vertical=config.vertical,
            schema_version=config.schema_version,
            base_version_id=request.base_version_id,
            benchmark_refs=benchmark_refs,
        )
        for field_name, evidence in base_fields.items():
            self.store.add_evidence(session_id, field_name, evidence)
        self._recalculate_status(session_id, config)
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> IntakeSessionView:
        row = self.store.get_session_row(session_id)
        config = self.configs.load(row["vertical"])
        fields = self.store.selected_evidence(session_id)
        return IntakeSessionView(
            session_id=row["session_id"],
            draft_id=row["draft_id"],
            vertical=row["vertical"],
            schema_version=row["schema_version"],
            status=row["status"],
            base_version_id=row["base_version_id"],
            fields=fields,
            evidence_candidates=self.store.evidence_candidates(session_id),
            unresolved_conflicts=self.store.unresolved_conflicts(session_id),
            pending_resolutions=self.store.pending_resolutions(session_id),
            missing_required_fields=self._missing_required(config, fields),
            benchmark_refs=self.store.benchmark_refs(session_id),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_confirmed_spec(self, version_id: str) -> ConfirmedJobSpecView:
        row = self.store.get_confirmed_row(version_id)
        facts = json.loads(row["facts_json"])
        return ConfirmedJobSpecView(
            version_id=row["version_id"],
            vertical=row["vertical"],
            schema_version=row["schema_version"],
            fields={
                name: EvidencedField.model_validate(field)
                for name, field in facts["fields"].items()
            },
            benchmark_refs=[
                BenchmarkRef.model_validate(item)
                for item in facts.get("benchmark_refs", [])
            ],
            confirmed_at=row["confirmed_at"],
            confirmed_by=row["confirmed_by"],
            canonical_hash=row["canonical_hash"],
        )

    async def start_voice(self, session_id: str) -> VoiceTurnResponse:
        self._assert_draft_mutable(session_id)
        response = self._voice_response(session_id, disclosure=AI_DISCLOSURE)
        provider, connection_url = await self.voice_adapter.create_connection(
            session_id=session_id,
            context={
                "vertical": response.session.vertical,
                "schema_version": response.session.schema_version,
                "disclosure": AI_DISCLOSURE,
                "next_field": response.next_field,
                "question_objective": response.next_question_objective,
                "spoken_question": response.spoken_question,
            },
        )
        return response.model_copy(
            update={
                "voice_provider": provider,
                "provider_connection_url": connection_url,
                "provider_context": {
                    "intake_session_id": session_id,
                    "vertical": response.session.vertical,
                    "schema_version": response.session.schema_version,
                    "disclosure": AI_DISCLOSURE,
                    "next_field": response.next_field,
                    "question_objective": response.next_question_objective,
                    "spoken_question": response.spoken_question,
                },
            }
        )

    def apply_voice_turn(
        self, session_id: str, request: VoiceTurnRequest
    ) -> VoiceTurnResponse:
        row = self._assert_draft_mutable(session_id)
        config = self.configs.load(row["vertical"])
        captured_at = self.store.add_voice_turn(
            session_id, request.turn_id, request.user_text
        )
        if request.field_name:
            self._validate_field(config, request.field_name)
            if request.mark_unknown:
                if not self._text_supports_unknown(request.user_text):
                    raise EstimatorValidationError(
                        "The source turn does not state that the field is unknown"
                    )
                value = "unknown"
                confidence = FieldConfidence.UNKNOWN
            else:
                if not self._value_supported_by_text(request.value, request.user_text):
                    raise EstimatorValidationError(
                        "Voice evidence value is not present in the referenced turn"
                    )
                value = request.value
                confidence = FieldConfidence.EXPLICIT
            evidence = EvidencedField(
                value=value,
                source=EvidenceSource(
                    modality=EvidenceModality.VOICE,
                    reference=request.turn_id,
                    captured_at=captured_at,
                ),
                confidence=confidence,
                unknown_acknowledged=(
                    request.unknown_acknowledged if request.mark_unknown else False
                ),
            )
            self.store.add_evidence(session_id, request.field_name, evidence)
        self._recalculate_status(session_id, config)
        return self._voice_response(session_id)

    async def apply_document(
        self,
        session_id: str,
        *,
        document_type: str,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> DocumentParseResult:
        row = self._assert_draft_mutable(session_id)
        config = self.configs.load(row["vertical"])
        if document_type not in config.document_types:
            raise EstimatorValidationError(
                f"Document type {document_type!r} is not enabled for {config.vertical}"
            )
        if not content:
            raise EstimatorValidationError("Uploaded document is empty")
        document_id = f"doc_{uuid4().hex}"
        parsed = await self.document_parser.parse(
            document_id=document_id,
            document_type=document_type,
            content=content,
            media_type=media_type,
        )
        accepted: list[str] = []
        review: list[str] = []
        for item in parsed:
            self._validate_field(config, item.field_name)
            needs_review = (
                item.confidence_score
                < config.document_types[document_type].review_threshold
            )
            evidence = EvidencedField(
                value=item.value,
                source=EvidenceSource(
                    modality=EvidenceModality.DOCUMENT,
                    reference={
                        "document_id": document_id,
                        "filename": filename,
                        "document_type": document_type,
                        "region": item.region,
                    },
                    captured_at=datetime.now(UTC),
                ),
                confidence=(
                    FieldConfidence.USER_CONFIRMED_SUGGESTION
                    if needs_review
                    else FieldConfidence.EXPLICIT
                ),
            )
            self.store.add_evidence(
                session_id,
                item.field_name,
                evidence,
                review_required=needs_review,
            )
            (review if needs_review else accepted).append(item.field_name)
        self._recalculate_status(session_id, config)
        return DocumentParseResult(
            session=self.get_session(session_id),
            document_id=document_id,
            accepted_fields=accepted,
            review_required_fields=review,
        )

    async def start_catalog_resolution(
        self,
        session_id: str,
        *,
        field_name: str,
        raw_statement: str,
        catalog_name: str,
    ):
        row = self._assert_draft_mutable(session_id)
        config = self.configs.load(row["vertical"])
        self._validate_field(config, field_name)
        enabled = config.catalog_resolvers.get(field_name, [])
        if catalog_name not in enabled:
            raise EstimatorValidationError(
                f"Catalog {catalog_name!r} is not enabled for {field_name!r}"
            )
        candidates = await self.catalog_resolver.search(
            field_name=field_name,
            raw_statement=raw_statement,
            catalog_name=catalog_name,
        )
        resolution = self.store.create_resolution(
            session_id,
            field_name,
            raw_statement,
            catalog_name,
            candidates[: config.catalog_candidate_limit],
        )
        self._recalculate_status(session_id, config)
        return resolution

    def select_catalog_candidate(
        self,
        session_id: str,
        *,
        resolution_id: str,
        candidate_id: str,
    ) -> IntakeSessionView:
        row = self._assert_draft_mutable(session_id)
        config = self.configs.load(row["vertical"])
        resolution_row = self.store.get_resolution(session_id, resolution_id)
        candidates = [
            CatalogCandidate.model_validate(item)
            for item in json.loads(resolution_row["candidates_json"])
        ]
        selected = next(
            (item for item in candidates if item.candidate_id == candidate_id), None
        )
        if selected is None:
            raise EstimatorValidationError("Selected candidate was not offered")
        resolution = CatalogResolution(
            raw_user_statement=resolution_row["raw_statement"],
            catalog_queried=resolution_row["catalog_name"],
            candidates=tuple(candidates),
            selected_candidate=selected,
            selected_by="user_confirmation",
        )
        evidence = EvidencedField(
            value=selected.canonical_value,
            source=EvidenceSource(
                modality=EvidenceModality.CATALOG_RESOLUTION,
                reference=resolution_id,
                captured_at=datetime.now(UTC),
            ),
            confidence=FieldConfidence.USER_CONFIRMED_SUGGESTION,
            resolution=resolution,
        )
        evidence_id = self.store.add_evidence(
            session_id, resolution_row["field_name"], evidence
        )
        self.store.select_evidence(
            session_id, resolution_row["field_name"], evidence_id
        )
        self.store.complete_resolution(session_id, resolution_id, candidate_id)
        self._recalculate_status(session_id, config)
        return self.get_session(session_id)

    def confirm(
        self, session_id: str, request: ConfirmSpecRequest
    ) -> ConfirmedJobSpecView:
        row = self._assert_draft_mutable(session_id)
        config = self.configs.load(row["vertical"])
        for selection in request.evidence_selections:
            self.store.select_evidence(
                session_id, selection.field_name, selection.evidence_id
            )
        self._recalculate_status(session_id, config)
        view = self.get_session(session_id)
        if view.status != IntakeStatus.AWAITING_CONFIRMATION:
            raise EstimatorValidationError(
                "Spec cannot be confirmed until every required field is evidenced "
                "or acknowledged unknown and all conflicts are resolved"
            )
        self._audit_evidence(view)
        version_id = f"spec_{uuid4().hex}"
        confirmed_at = datetime.now(UTC)
        facts = {
            "vertical": view.vertical,
            "schema_version": view.schema_version,
            "fields": {
                name: field.model_dump(mode="json")
                for name, field in sorted(view.fields.items())
            },
            "benchmark_refs": [
                item.model_dump(mode="json") for item in view.benchmark_refs
            ],
        }
        canonical_hash = self._caller_compatible_hash(version_id, facts)
        self.store.save_confirmed(
            session_id=session_id,
            version_id=version_id,
            facts=facts,
            confirmed_at=confirmed_at,
            confirmed_by=request.confirmed_by,
            canonical_hash=canonical_hash,
        )
        return ConfirmedJobSpecView(
            version_id=version_id,
            vertical=view.vertical,
            schema_version=view.schema_version,
            fields=view.fields,
            benchmark_refs=view.benchmark_refs,
            confirmed_at=confirmed_at,
            confirmed_by=request.confirmed_by,
            canonical_hash=canonical_hash,
        )

    def _voice_response(
        self, session_id: str, *, disclosure: str | None = None
    ) -> VoiceTurnResponse:
        view = self.get_session(session_id)
        config = self.configs.load(view.vertical)
        plan = self.questions.plan(
            config, view.fields, self.store.question_attempts(session_id)
        )
        if plan is None:
            return VoiceTurnResponse(session=view, disclosure=disclosure)
        self.store.increment_question_attempt(session_id, plan.field_name)
        return VoiceTurnResponse(
            session=view,
            disclosure=disclosure,
            next_field=plan.field_name,
            next_question_objective=plan.objective,
            spoken_question=plan.spoken_question,
        )

    def _recalculate_status(
        self, session_id: str, config: VerticalConfig
    ) -> IntakeStatus:
        fields = self.store.selected_evidence(session_id)
        pending = [
            item
            for item in self.store.pending_resolutions(session_id)
            if item.status == "pending"
        ]
        if pending:
            status = IntakeStatus.RESOLVING
        elif not self._missing_required(config, fields) and not self.store.unresolved_conflicts(
            session_id
        ):
            status = IntakeStatus.AWAITING_CONFIRMATION
        else:
            status = IntakeStatus.DRAFT
        self.store.update_status(session_id, status)
        return status

    @staticmethod
    def _missing_required(
        config: VerticalConfig, fields: dict[str, EvidencedField]
    ) -> list[str]:
        return [
            name
            for name in config.required_fields
            if name not in fields
            or fields[name].value == "unknown"
            and not fields[name].unknown_acknowledged
        ]

    @staticmethod
    def _audit_evidence(view: IntakeSessionView) -> None:
        if view.unresolved_conflicts:
            raise EstimatorValidationError("Unresolved evidence conflicts remain")
        if any(item.status == "pending" for item in view.pending_resolutions):
            raise EstimatorValidationError("Catalog selection is still pending")
        for name, field in view.fields.items():
            if field.source.modality == EvidenceModality.VOICE and not isinstance(
                field.source.reference, str
            ):
                raise EstimatorValidationError(f"Voice evidence for {name} lacks a turn ID")
            if field.source.modality == EvidenceModality.DOCUMENT:
                reference = field.source.reference
                if not isinstance(reference, dict) or not reference.get("region"):
                    raise EstimatorValidationError(
                        f"Document evidence for {name} lacks a document region"
                    )
            if field.source.modality == EvidenceModality.CATALOG_RESOLUTION:
                if field.resolution is None or field.resolution.selected_by != "user_confirmation":
                    raise EstimatorValidationError(
                        f"Catalog evidence for {name} was not user selected"
                    )

    @staticmethod
    def _caller_compatible_hash(version_id: str, facts: dict[str, Any]) -> str:
        payload = {
            "version_id": version_id,
            "status": "confirmed",
            "facts": facts,
        }
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _value_supported_by_text(value: Any, text: str) -> bool:
        if isinstance(value, (dict, list)):
            return False
        value_text = str(value).strip().casefold()
        source = text.casefold()
        if value_text in source:
            return True
        normalized_value = re.sub(r"[^a-z0-9]", "", value_text)
        normalized_source = re.sub(r"[^a-z0-9]", "", source)
        return bool(normalized_value and normalized_value in normalized_source)

    @staticmethod
    def _text_supports_unknown(text: str) -> bool:
        return bool(
            re.search(
                r"\b(don't know|do not know|not sure|unknown|no idea|don't have)\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _validate_field(config: VerticalConfig, field_name: str) -> None:
        if field_name not in config.fields:
            raise EstimatorValidationError(
                f"Field {field_name!r} is not part of the {config.vertical} schema"
            )

    @staticmethod
    def _validate_benchmark_refs(
        config: VerticalConfig, refs: list[BenchmarkRef]
    ) -> None:
        for ref in refs:
            if ref.benchmark_source not in config.benchmark_sources:
                raise EstimatorValidationError(
                    f"Benchmark source {ref.benchmark_source!r} is not enabled"
                )
            if "price" in ref.model_dump():
                raise EstimatorValidationError("Benchmark values cannot be stored inline")

    def _assert_draft_mutable(self, session_id: str):
        row = self.store.get_session_row(session_id)
        if row["status"] in {
            IntakeStatus.CONFIRMED.value,
            IntakeStatus.SUPERSEDED.value,
        }:
            raise EstimatorConflictError("Confirmed specifications are immutable")
        return row
