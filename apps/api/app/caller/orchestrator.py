from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from .errors import ImmutableSpecificationError, NotFoundError, ValidationError
from .outcome_validator import CallOutcomeValidator
from .ports import CallerInputGateway, CallerStore, VoiceSessionAdapter
from .quote_draft import QuoteDraftManager
from .schemas import (
    AgentEvent,
    AgentEventType,
    CallContext,
    CallCreateRequest,
    CallRecord,
    CallStatus,
    CallView,
    ConfirmedJobSpec,
    OutcomeType,
    QuoteLineItem,
    QuoteLineItemInput,
    QuoteTerm,
    QuoteTermInput,
    StructuredCallOutcome,
    TranscriptEvent,
)
from .state_machine import CallStateMachine, TERMINAL_STATES


CALLER_TOOL_NAMES = (
    "get_confirmed_job_spec",
    "log_quote_line_item",
    "log_quote_term",
    "log_quote_assumption",
    "log_vendor_red_flag",
    "record_callback_commitment",
    "record_vendor_decline",
    "finalize_quote",
)


def specification_sha256(spec: ConfirmedJobSpec) -> str:
    immutable_payload = {
        "version_id": spec.version_id,
        "status": spec.status,
        "facts": spec.facts,
    }
    canonical = json.dumps(
        immutable_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class CallOrchestrator:
    def __init__(
        self,
        store: CallerStore,
        inputs: CallerInputGateway,
        adapter: VoiceSessionAdapter,
    ) -> None:
        self.store = store
        self.inputs = inputs
        self.adapter = adapter
        self.states = CallStateMachine()
        self.quotes = QuoteDraftManager(store)
        self.outcomes = CallOutcomeValidator()

    def create_call(self, request: CallCreateRequest) -> CallRecord:
        spec = self.inputs.get_confirmed_job_spec(request.job_spec_version_id)
        if spec is None:
            raise ValidationError(
                "Call creation requires an existing confirmed job specification"
            )
        vendor = self.inputs.get_vendor_target(request.vendor_id)
        if vendor is None:
            raise NotFoundError(f"Vendor {request.vendor_id!r} was not found")
        if any(
            bid.job_spec_version_id != spec.version_id
            for bid in request.policy.verified_competing_bids
        ):
            raise ValidationError(
                "Competing-bid leverage must reference the same confirmed job "
                "specification version"
            )

        call = CallRecord(
            call_id=f"call_{uuid4().hex}",
            vendor_id=vendor.vendor_id,
            job_spec_version_id=spec.version_id,
            spec_sha256=specification_sha256(spec),
            call_type=request.call_type,
            status=CallStatus.CREATED,
            transcript_id=f"tx_{uuid4().hex}",
            policy=request.policy,
            agent_configuration=request.agent_configuration,
            created_at=datetime.now(UTC),
        )
        self.store.create_call(call)
        self.store.create_quote_draft(call.call_id)
        self.store.append_call_event(
            call.call_id,
            "call_created",
            {"spec_sha256": call.spec_sha256, "vendor_id": call.vendor_id},
        )
        return call

    async def start_call(self, call_id: str) -> CallContext:
        call = self._get_call(call_id)
        spec = self._verified_spec(call)
        vendor = self.inputs.get_vendor_target(call.vendor_id)
        if vendor is None:
            raise NotFoundError(f"Vendor {call.vendor_id!r} was not found")
        self.states.assert_transition(call.status, CallStatus.CONNECTING)

        connecting = call.model_copy(
            update={"status": CallStatus.CONNECTING, "started_at": datetime.now(UTC)}
        )
        self.store.update_call(connecting)
        context = CallContext(
            call_id=call.call_id,
            immutable_spec_sha256=call.spec_sha256,
            confirmed_job_spec=spec,
            vendor=vendor,
            policy=call.policy,
            agent_configuration=call.agent_configuration,
            tool_names=CALLER_TOOL_NAMES,
        )
        try:
            session_id = await self.adapter.create_session(context)
            await self.adapter.send_context(session_id, context)
        except Exception:
            failed = connecting.model_copy(
                update={"status": CallStatus.FAILED, "ended_at": datetime.now(UTC)}
            )
            self.store.update_call(failed)
            self.store.append_call_event(call_id, "adapter_start_failed", {})
            raise

        started = connecting.model_copy(update={"provider_session_id": session_id})
        self.store.update_call(started)
        self.store.append_call_event(
            call_id, "session_created", {"provider_session_id": session_id}
        )
        return context

    def handle_agent_event(
        self, call_id: str, event: AgentEvent
    ) -> TranscriptEvent | CallRecord:
        call = self._get_call(call_id)
        self._verified_spec(call)
        self.store.append_call_event(
            call_id,
            event.type.value,
            event.model_dump(mode="json", exclude_none=True),
        )

        if event.type == AgentEventType.TRANSCRIPT:
            if event.transcript is None:
                raise ValidationError("Transcript events require transcript content")
            if call.status in TERMINAL_STATES:
                raise ValidationError("Cannot append transcript to a terminal call")
            if call.status in {CallStatus.CREATED, CallStatus.CONNECTING}:
                raise ValidationError(
                    "Transcript cannot be logged before the voice connection is established"
                )
            return self.store.append_transcript(call_id, event.transcript)

        if event.type == AgentEventType.CONNECTION_ESTABLISHED:
            return self._transition(call, CallStatus.DISCLOSURE)

        if event.type == AgentEventType.CONNECTION_FAILED:
            return self._transition(call, CallStatus.FAILED, ended=True)

        if event.type == AgentEventType.PHASE_COMPLETED:
            if event.phase is not None and event.phase != call.status:
                raise ValidationError(
                    f"Completed phase {event.phase.value} does not match current "
                    f"state {call.status.value}"
                )
            return self._transition(call, self.states.next_phase(call.status))

        return call

    def log_quote_line_item(
        self, call_id: str, item: QuoteLineItemInput
    ) -> QuoteLineItem:
        call = self._get_mutable_call(call_id)
        self._verified_spec(call)
        if call.status not in {
            CallStatus.QUOTE_COLLECTION,
            CallStatus.QUOTE_CLARIFICATION,
            CallStatus.SUMMARY_CONFIRMATION,
        }:
            raise ValidationError("Line items can only be logged during quote collection")
        result = self.quotes.log_line_item(call_id, item)
        self.store.append_call_event(
            call_id, "quote_line_item_logged", {"line_item_id": result.line_item_id}
        )
        return result

    def log_quote_term(self, call_id: str, term: QuoteTermInput) -> QuoteTerm:
        call = self._get_mutable_call(call_id)
        self._verified_spec(call)
        quote_states = {
            CallStatus.QUOTE_COLLECTION,
            CallStatus.QUOTE_CLARIFICATION,
            CallStatus.SUMMARY_CONFIRMATION,
        }
        early_exit_categories = {"callback", "decline"}
        if term.category not in early_exit_categories and call.status not in quote_states:
            raise ValidationError("Quote terms can only be logged during quote collection")
        if term.category in early_exit_categories and call.status in {
            CallStatus.CREATED,
            CallStatus.CONNECTING,
        }:
            raise ValidationError("Vendor response cannot be logged before disclosure")
        result = self.quotes.log_term(call_id, term)
        self.store.append_call_event(
            call_id,
            "quote_term_logged",
            {"term_id": result.term_id, "category": result.category},
        )
        return result

    async def finalize_call(
        self, call_id: str, requested: OutcomeType | None = None
    ) -> StructuredCallOutcome:
        call = self._get_call(call_id)
        existing = self.store.get_outcome(call_id)
        if existing is not None:
            return existing
        self._verified_spec(call)
        if call.status == CallStatus.FAILED:
            raise ValidationError("A failed connection cannot be finalized as a conversation")
        if call.status in TERMINAL_STATES:
            raise ValidationError("Terminal call has no stored structured outcome")

        quote = self.store.get_quote_draft(call_id)
        transcript = self.store.list_transcript(call_id)
        outcome_type = requested or self.outcomes.infer(quote)
        validated = self.outcomes.validate(
            outcome_type, quote, transcript, call.policy
        )
        target_state = {
            OutcomeType.COMPLETE_QUOTE: CallStatus.COMPLETE,
            OutcomeType.CALLBACK_REQUIRED: CallStatus.CALLBACK_REQUIRED,
            OutcomeType.DECLINED: CallStatus.DECLINED,
            OutcomeType.INCOMPLETE_QUOTE: CallStatus.INCOMPLETE,
        }[outcome_type]
        self.states.assert_transition(call.status, target_state)

        recording = None
        if call.provider_session_id:
            await self.adapter.end_session(call.provider_session_id)
            recording = await self.adapter.get_recording_reference(
                call.provider_session_id
            )
        final_quote = self.store.finalize_quote(call_id)
        outcome = StructuredCallOutcome(
            outcome_id=f"out_{uuid4().hex}",
            call_id=call_id,
            outcome_type=outcome_type,
            quote_version_id=final_quote.quote_version_id,
            reason=validated.reason,
            validation_warnings=validated.warnings,
            created_at=datetime.now(UTC),
        )
        self.store.save_outcome(outcome)
        final_call = call.model_copy(
            update={
                "status": target_state,
                "ended_at": datetime.now(UTC),
                "recording_id": recording,
                "final_outcome": outcome_type,
            }
        )
        self.store.update_call(final_call)
        self.store.append_call_event(
            call_id,
            "call_finalized",
            {"outcome_type": outcome_type.value, "warnings": validated.warnings},
        )
        return outcome

    def get_call_view(self, call_id: str) -> CallView:
        call = self._get_call(call_id)
        return CallView(
            call=call,
            transcript=self.store.list_transcript(call_id),
            original_quote=self.store.get_quote_draft(call_id),
            outcome=self.store.get_outcome(call_id),
            recording_reference=call.recording_id,
        )

    def get_confirmed_job_spec(self, call_id: str) -> ConfirmedJobSpec:
        return self._verified_spec(self._get_call(call_id))

    def _transition(
        self, call: CallRecord, target: CallStatus, ended: bool = False
    ) -> CallRecord:
        self.states.assert_transition(call.status, target)
        updated = call.model_copy(
            update={
                "status": target,
                "ended_at": datetime.now(UTC) if ended else call.ended_at,
            }
        )
        self.store.update_call(updated)
        return updated

    def _get_call(self, call_id: str) -> CallRecord:
        call = self.store.get_call(call_id)
        if call is None:
            raise NotFoundError(f"Call {call_id!r} was not found")
        return call

    def _get_mutable_call(self, call_id: str) -> CallRecord:
        call = self._get_call(call_id)
        if call.status in TERMINAL_STATES:
            raise ValidationError("Cannot update quote data for a terminal call")
        return call

    def _verified_spec(self, call: CallRecord) -> ConfirmedJobSpec:
        spec = self.inputs.get_confirmed_job_spec(call.job_spec_version_id)
        if spec is None:
            raise ImmutableSpecificationError(
                "The confirmed specification is no longer available"
            )
        current_hash = specification_sha256(spec)
        if current_hash != call.spec_sha256:
            raise ImmutableSpecificationError(
                "Confirmed specification changed after call creation"
            )
        return spec
