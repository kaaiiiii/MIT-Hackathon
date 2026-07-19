from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CallStatus(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    DISCLOSURE = "disclosure"
    JOB_PRESENTATION = "job_presentation"
    QUOTE_COLLECTION = "quote_collection"
    QUOTE_CLARIFICATION = "quote_clarification"
    SUMMARY_CONFIRMATION = "summary_confirmation"
    COMPLETE = "complete"
    CALLBACK_REQUIRED = "callback_required"
    DECLINED = "declined"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class OutcomeType(StrEnum):
    COMPLETE_QUOTE = "complete_quote"
    CALLBACK_REQUIRED = "callback_required"
    DECLINED = "declined"
    INCOMPLETE_QUOTE = "incomplete_quote"


class EvidenceConfidence(StrEnum):
    EXPLICIT = "explicit"
    CONFIRMED_READBACK = "confirmed_readback"
    INFERRED = "inferred"


class ConfirmedJobSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    version_id: str
    status: Literal["confirmed"]
    facts: dict[str, Any]
    confirmed_at: datetime | None = None


class VendorTarget(BaseModel):
    model_config = ConfigDict(frozen=True)

    vendor_id: str
    name: str
    phone: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerifiedCompetingBid(BaseModel):
    """Comparison-owned leverage that the Caller is explicitly allowed to state."""

    model_config = ConfigDict(frozen=True)

    bid_id: str = Field(min_length=1)
    source_call_id: str = Field(min_length=1)
    job_spec_version_id: str = Field(min_length=1)
    total: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    binding_status: Literal["binding", "non_binding", "unclear"] = "unclear"
    evidence_reference: str = Field(min_length=1)


class CallPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    disclose_ai: Literal[True] = True
    max_duration_seconds: int = Field(default=900, ge=30, le=3600)
    require_itemization: bool = True
    probe_hidden_fees: bool = True
    allow_callback: bool = True
    custom_questions: tuple[str, ...] = ()
    verified_competing_bids: tuple[VerifiedCompetingBid, ...] = ()
    approved_leverage_bid_id: str | None = None

    @model_validator(mode="after")
    def approved_bid_must_be_verified(self) -> "CallPolicy":
        if self.approved_leverage_bid_id is None:
            return self
        matching = [
            bid
            for bid in self.verified_competing_bids
            if bid.bid_id == self.approved_leverage_bid_id
        ]
        if len(matching) != 1:
            raise ValueError(
                "approved_leverage_bid_id must identify exactly one verified bid"
            )
        return self


class AgentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    adapter: str = "simulated"
    voice_id: str | None = None
    language: str = "en-US"
    model: str | None = None


class CallCreateRequest(BaseModel):
    job_spec_version_id: str
    vendor_id: str
    call_type: str = "initial_quote"
    policy: CallPolicy = Field(default_factory=CallPolicy)
    agent_configuration: AgentConfiguration = Field(default_factory=AgentConfiguration)


class TranscriptEventInput(BaseModel):
    event_id: str | None = None
    speaker: Literal["agent", "vendor", "system"]
    text: str = Field(min_length=1)
    timestamp_seconds: float = Field(ge=0)
    sequence: int = Field(ge=0)


class AgentEventType(StrEnum):
    TRANSCRIPT = "transcript"
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_FAILED = "connection_failed"
    PHASE_COMPLETED = "phase_completed"
    INTERRUPTION = "interruption"
    SESSION_ENDED = "session_ended"


class AgentEvent(BaseModel):
    type: AgentEventType
    transcript: TranscriptEventInput | None = None
    phase: CallStatus | None = None
    detail: str | None = None
    provider_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceInput(BaseModel):
    transcript_event_id: str
    timestamp_seconds: float = Field(ge=0)
    confidence: EvidenceConfidence = EvidenceConfidence.EXPLICIT
    source: Literal["vendor_statement", "vendor_confirmation"] = "vendor_statement"


class QuoteLineItemInput(BaseModel):
    category: str
    description: str
    amount: float | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    currency: str = "USD"
    evidence: EvidenceInput


class QuoteTermInput(BaseModel):
    category: Literal[
        "pricing_model",
        "fee",
        "discount",
        "term",
        "assumption",
        "exclusion",
        "red_flag",
        "callback",
        "decline",
        "availability",
        "binding_status",
        "estimated_total",
        "vendor_requirement",
        "itemization_status",
    ]
    key: str
    value: Any
    evidence: EvidenceInput


class FinalizeCallRequest(BaseModel):
    requested_outcome: OutcomeType | None = None


class CallRecord(BaseModel):
    call_id: str
    vendor_id: str
    job_spec_version_id: str
    spec_sha256: str
    call_type: str
    status: CallStatus
    provider_session_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    transcript_id: str
    recording_id: str | None = None
    final_outcome: OutcomeType | None = None
    policy: CallPolicy
    agent_configuration: AgentConfiguration
    created_at: datetime


class TranscriptEvent(BaseModel):
    event_id: str
    call_id: str
    speaker: str
    text: str
    timestamp_seconds: float
    sequence: int
    created_at: datetime


class QuoteLineItem(QuoteLineItemInput):
    line_item_id: str
    quote_version_id: str


class QuoteTerm(QuoteTermInput):
    term_id: str
    quote_version_id: str


class QuoteDraft(BaseModel):
    quote_version_id: str
    call_id: str
    version: int
    status: Literal["draft", "final"]
    line_items: list[QuoteLineItem] = Field(default_factory=list)
    terms: list[QuoteTerm] = Field(default_factory=list)


class StructuredCallOutcome(BaseModel):
    outcome_id: str
    call_id: str
    outcome_type: OutcomeType
    quote_version_id: str | None = None
    reason: str | None = None
    validation_warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class CallContext(BaseModel):
    call_id: str
    immutable_spec_sha256: str
    confirmed_job_spec: ConfirmedJobSpec
    vendor: VendorTarget
    policy: CallPolicy
    agent_configuration: AgentConfiguration
    tool_names: tuple[str, ...]
    augmented_context: dict[str, Any] = Field(default_factory=dict)


class CallView(BaseModel):
    call: CallRecord
    transcript: list[TranscriptEvent]
    original_quote: QuoteDraft
    dialogue_actions: list[str] = Field(default_factory=list)
    outcome: StructuredCallOutcome | None = None
    recording_reference: str | None = None
    augmented_context: dict[str, Any] = Field(default_factory=dict)
