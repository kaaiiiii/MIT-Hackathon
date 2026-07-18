import pytest

from apps.api.app.caller.errors import (
    ImmutableSpecificationError,
    ValidationError,
)
from apps.api.app.caller.schemas import (
    AgentEvent,
    AgentEventType,
    CallCreateRequest,
    CallStatus,
    ConfirmedJobSpec,
    EvidenceInput,
    OutcomeType,
    QuoteLineItemInput,
    QuoteTermInput,
    TranscriptEventInput,
)


def create(caller):
    return caller.create_call(
        CallCreateRequest(
            job_spec_version_id="spec_123", vendor_id="vendor_transparent"
        )
    )


async def move_to_quote_collection(caller, call_id: str) -> None:
    await caller.start_call(call_id)
    caller.handle_agent_event(
        call_id, AgentEvent(type=AgentEventType.CONNECTION_ESTABLISHED)
    )
    caller.handle_agent_event(
        call_id,
        AgentEvent(type=AgentEventType.PHASE_COMPLETED, phase=CallStatus.DISCLOSURE),
    )
    caller.handle_agent_event(
        call_id,
        AgentEvent(
            type=AgentEventType.PHASE_COMPLETED,
            phase=CallStatus.JOB_PRESENTATION,
        ),
    )


def vendor_statement(caller, call_id: str, event_id: str, text: str, t: float, seq: int):
    return caller.handle_agent_event(
        call_id,
        AgentEvent(
            type=AgentEventType.TRANSCRIPT,
            transcript=TranscriptEventInput(
                event_id=event_id,
                speaker="vendor",
                text=text,
                timestamp_seconds=t,
                sequence=seq,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_complete_quote_is_evidence_backed_and_finalized(caller):
    call = create(caller)
    await move_to_quote_collection(caller, call.call_id)
    vendor_statement(
        caller,
        call.call_id,
        "te_price",
        "Labor is $125 and the total is $150 including travel.",
        93.4,
        1,
    )
    evidence = EvidenceInput(
        transcript_event_id="te_price", timestamp_seconds=93.4
    )
    caller.log_quote_line_item(
        call.call_id,
        QuoteLineItemInput(
            category="labor", description="Piano moving labor", amount=125, evidence=evidence
        ),
    )
    for category, key, value in [
        ("estimated_total", "total", 150),
        ("binding_status", "status", "estimate"),
        ("availability", "date", "2026-08-01"),
    ]:
        caller.log_quote_term(
            call.call_id,
            QuoteTermInput(category=category, key=key, value=value, evidence=evidence),
        )

    caller.handle_agent_event(
        call.call_id,
        AgentEvent(
            type=AgentEventType.PHASE_COMPLETED,
            phase=CallStatus.QUOTE_COLLECTION,
        ),
    )
    caller.handle_agent_event(
        call.call_id,
        AgentEvent(
            type=AgentEventType.PHASE_COMPLETED,
            phase=CallStatus.QUOTE_CLARIFICATION,
        ),
    )
    outcome = await caller.finalize_call(call.call_id)

    assert outcome.outcome_type == OutcomeType.COMPLETE_QUOTE
    assert outcome.validation_warnings == []
    view = caller.get_call_view(call.call_id)
    assert view.call.status == CallStatus.COMPLETE
    assert view.call.recording_id.startswith("simulated://recordings/")
    assert view.original_quote.status == "final"
    assert view.original_quote.line_items[0].evidence.transcript_event_id == "te_price"


@pytest.mark.asyncio
async def test_quote_claim_rejects_non_vendor_evidence(caller):
    call = create(caller)
    await move_to_quote_collection(caller, call.call_id)
    caller.handle_agent_event(
        call.call_id,
        AgentEvent(
            type=AgentEventType.TRANSCRIPT,
            transcript=TranscriptEventInput(
                event_id="te_agent",
                speaker="agent",
                text="So that is $150?",
                timestamp_seconds=10,
                sequence=1,
            ),
        ),
    )
    with pytest.raises(ValidationError, match="vendor transcript"):
        caller.log_quote_term(
            call.call_id,
            QuoteTermInput(
                category="estimated_total",
                key="total",
                value=150,
                evidence=EvidenceInput(
                    transcript_event_id="te_agent", timestamp_seconds=10
                ),
            ),
        )


@pytest.mark.asyncio
async def test_spec_hash_detects_upstream_mutation(caller):
    call = create(caller)
    caller.inputs.specs["spec_123"] = ConfirmedJobSpec(
        version_id="spec_123",
        status="confirmed",
        facts={"service": "a different job"},
    )
    with pytest.raises(ImmutableSpecificationError, match="changed"):
        await caller.start_call(call.call_id)


def test_call_creation_requires_confirmed_spec(caller):
    with pytest.raises(ValidationError, match="confirmed job specification"):
        caller.create_call(
            CallCreateRequest(
                job_spec_version_id="missing", vendor_id="vendor_transparent"
            )
        )


@pytest.mark.asyncio
async def test_callback_requires_evidence_backed_commitment(caller):
    call = create(caller)
    await caller.start_call(call.call_id)
    caller.handle_agent_event(
        call.call_id, AgentEvent(type=AgentEventType.CONNECTION_ESTABLISHED)
    )
    vendor_statement(
        caller,
        call.call_id,
        "te_callback",
        "Our estimator will call tomorrow morning.",
        12,
        1,
    )
    evidence = EvidenceInput(
        transcript_event_id="te_callback", timestamp_seconds=12
    )
    caller.log_quote_term(
        call.call_id,
        QuoteTermInput(
            category="callback",
            key="commitment",
            value="tomorrow morning",
            evidence=evidence,
        ),
    )
    outcome = await caller.finalize_call(call.call_id)
    assert outcome.outcome_type == OutcomeType.CALLBACK_REQUIRED
    assert caller.get_call_view(call.call_id).call.status == CallStatus.CALLBACK_REQUIRED

