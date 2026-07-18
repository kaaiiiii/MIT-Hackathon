from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntakeStatus(StrEnum):
    DRAFT = "draft"
    RESOLVING = "resolving"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class EvidenceModality(StrEnum):
    VOICE = "voice"
    DOCUMENT = "document"
    CATALOG_RESOLUTION = "catalog_resolution"


class FieldConfidence(StrEnum):
    EXPLICIT = "explicit"
    ELICITED = "elicited"
    USER_CONFIRMED_SUGGESTION = "user_confirmed_suggestion"
    UNKNOWN = "unknown"


class EvidenceSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    modality: EvidenceModality
    reference: str | dict[str, Any]
    captured_at: datetime

    @model_validator(mode="after")
    def reference_must_be_specific(self) -> "EvidenceSource":
        if isinstance(self.reference, str) and not self.reference.strip():
            raise ValueError("Evidence reference cannot be empty")
        if isinstance(self.reference, dict) and not self.reference:
            raise ValueError("Evidence reference cannot be empty")
        return self


class CatalogCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    canonical_value: Any
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogResolution(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_user_statement: str = Field(min_length=1)
    catalog_queried: str = Field(min_length=1)
    candidates: tuple[CatalogCandidate, ...]
    selected_candidate: CatalogCandidate
    selected_by: Literal["user_confirmation"]

    @model_validator(mode="after")
    def selection_must_come_from_candidates(self) -> "CatalogResolution":
        if self.selected_candidate.candidate_id not in {
            candidate.candidate_id for candidate in self.candidates
        }:
            raise ValueError("Selected catalog candidate was not offered to the user")
        return self


class EvidencedField(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: Any
    source: EvidenceSource
    confidence: FieldConfidence
    resolution: CatalogResolution | None = None
    unknown_acknowledged: bool = False

    @model_validator(mode="after")
    def enforce_provenance_shape(self) -> "EvidencedField":
        is_unknown = self.value == "unknown"
        if is_unknown and self.confidence != FieldConfidence.UNKNOWN:
            raise ValueError("Unknown values require unknown confidence")
        if not is_unknown and self.confidence == FieldConfidence.UNKNOWN:
            raise ValueError("Unknown confidence requires an unknown value")
        if self.source.modality == EvidenceModality.CATALOG_RESOLUTION:
            if self.resolution is None:
                raise ValueError("Catalog evidence requires its full resolution chain")
            if self.resolution.selected_by != "user_confirmation":
                raise ValueError("Catalog values must be selected by the user")
        elif self.resolution is not None:
            raise ValueError("Only catalog evidence may carry a catalog resolution")
        return self


class BenchmarkRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str = Field(min_length=1)
    benchmark_source: str = Field(min_length=1)
    benchmark_key: str = Field(min_length=1)
    attached_at: datetime


class PendingCatalogResolution(BaseModel):
    resolution_id: str
    field_name: str
    raw_user_statement: str
    catalog_queried: str
    candidates: list[CatalogCandidate]
    status: Literal["pending", "no_candidates"]
    created_at: datetime


class IntakeSessionView(BaseModel):
    session_id: str
    draft_id: str
    vertical: str
    schema_version: str
    status: IntakeStatus
    base_version_id: str | None = None
    fields: dict[str, EvidencedField]
    evidence_candidates: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    pending_resolutions: list[PendingCatalogResolution] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    benchmark_refs: list[BenchmarkRef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConfirmedJobSpecView(BaseModel):
    version_id: str
    vertical: str
    schema_version: str
    status: Literal["confirmed"] = "confirmed"
    fields: dict[str, EvidencedField]
    benchmark_refs: list[BenchmarkRef]
    confirmed_at: datetime
    confirmed_by: str
    canonical_hash: str


class IntakeSessionCreate(BaseModel):
    vertical: str = "moving"
    base_version_id: str | None = None
    benchmark_refs: list[BenchmarkRef] = Field(default_factory=list)


class VoiceTurnRequest(BaseModel):
    turn_id: str = Field(min_length=1)
    user_text: str = Field(min_length=1)
    field_name: str | None = None
    value: Any = None
    mark_unknown: bool = False
    unknown_acknowledged: bool = False

    @model_validator(mode="after")
    def field_and_value_are_coherent(self) -> "VoiceTurnRequest":
        if self.mark_unknown and not self.field_name:
            raise ValueError("Marking unknown requires a field name")
        if self.field_name and self.value is None and not self.mark_unknown:
            raise ValueError("A field name requires a value or mark_unknown")
        return self


class VoiceTurnResponse(BaseModel):
    session: IntakeSessionView
    disclosure: str | None = None
    next_field: str | None = None
    next_question_objective: str | None = None
    spoken_question: str | None = None
    voice_provider: str | None = None
    provider_connection_url: str | None = None
    provider_context: dict[str, Any] = Field(default_factory=dict)


class CatalogResolveRequest(BaseModel):
    field_name: str | None = None
    raw_user_statement: str | None = None
    catalog_name: str | None = None
    resolution_id: str | None = None
    selected_candidate_id: str | None = None

    @model_validator(mode="after")
    def start_or_select(self) -> "CatalogResolveRequest":
        starting = self.field_name and self.raw_user_statement and self.catalog_name
        selecting = self.resolution_id and self.selected_candidate_id
        if bool(starting) == bool(selecting):
            raise ValueError(
                "Provide field/raw statement/catalog to start, or resolution/candidate to select"
            )
        return self


class EvidenceSelection(BaseModel):
    field_name: str
    evidence_id: str


class ConfirmSpecRequest(BaseModel):
    approved: Literal[True]
    confirmed_by: str = Field(min_length=1)
    evidence_selections: list[EvidenceSelection] = Field(default_factory=list)


class DocumentParseResult(BaseModel):
    session: IntakeSessionView
    document_id: str
    accepted_fields: list[str]
    review_required_fields: list[str]


class ParsedDocumentField(BaseModel):
    field_name: str
    value: Any
    region: dict[str, Any]
    confidence_score: float = Field(ge=0, le=1)
