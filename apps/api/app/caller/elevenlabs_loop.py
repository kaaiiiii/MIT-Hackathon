from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .adapters.elevenlabs_agent import ElevenLabsCallerAgentAdapter
from .dialogue_planner import DialogueAction
from .errors import ConflictError, ExternalServiceError, ValidationError
from .openai_agent import BuyerTurnAdvice, BuyerTurnAdvisor
from .orchestrator import CallOrchestrator
from .schemas import (
    AgentConfiguration,
    AgentEvent,
    AgentEventType,
    CallCreateRequest,
    CallPolicy,
    CallStatus,
    CallView,
    EvidenceInput,
    OutcomeType,
    QuoteLineItemInput,
    QuoteTermInput,
    TranscriptEventInput,
    VendorTarget,
    VerifiedCompetingBid,
)


class CallerAgentStartRequest(BaseModel):
    job_spec_version_id: str | None = None
    verified_competing_bid: VerifiedCompetingBid | None = None


class CallerAgentAdviceRequest(BaseModel):
    vendor_text: str = Field(min_length=1, max_length=4000)
    conversation_history: Any = None


class CallerAgentUtteranceRequest(BaseModel):
    spoken_text: str = Field(min_length=1, max_length=1200)
    advice_id: str | None = None


class CallerAgentResponse(BaseModel):
    call: CallView
    confirmed_job_facts: dict = Field(default_factory=dict)
    terminal: bool
    model: str
    voice_provider: str = "elevenlabs_agents"
    provider_connection_url: str | None = None
    provider_context: dict[str, Any] = Field(default_factory=dict)
    required_client_tools: tuple[str, ...] = (
        "advise_caller_turn",
        "record_caller_utterance",
    )
    advice_id: str | None = None
    advice: BuyerTurnAdvice | None = None
    recommended_outcome: OutcomeType | None = None


@dataclass(frozen=True)
class _PendingAdvice:
    call_id: str
    outcome: OutcomeType | None


class ElevenLabsCallerLoop:
    """ElevenLabs owns dialogue; GPT silently advises and the backend owns facts."""

    def __init__(
        self,
        *,
        orchestrator: CallOrchestrator,
        advisor: BuyerTurnAdvisor,
        connection_adapter: ElevenLabsCallerAgentAdapter,
        default_spec_version_id: str,
        vendor: VendorTarget,
    ) -> None:
        self.orchestrator = orchestrator
        self.advisor = advisor
        self.connection_adapter = connection_adapter
        self.default_spec_version_id = default_spec_version_id
        self.vendor = vendor
        self._job_facts_by_call: dict[str, dict] = {}
        self._pending: dict[str, _PendingAdvice] = {}

    async def start(
        self, request: CallerAgentStartRequest | None = None
    ) -> CallerAgentResponse:
        bid = request.verified_competing_bid if request else None
        version_id = (
            request.job_spec_version_id
            if request and request.job_spec_version_id
            else self.default_spec_version_id
        )
        spec = self.orchestrator.inputs.get_confirmed_job_spec(version_id)
        if spec is None:
            raise ValidationError(
                "Caller Lab requires an existing confirmed Estimator specification"
            )
        policy = CallPolicy(
            verified_competing_bids=(bid,) if bid else (),
            approved_leverage_bid_id=bid.bid_id if bid else None,
        )
        call = self.orchestrator.create_call(
            CallCreateRequest(
                job_spec_version_id=version_id,
                vendor_id=self.vendor.vendor_id,
                call_type="elevenlabs_agent_demo",
                policy=policy,
                agent_configuration=AgentConfiguration(
                    adapter="elevenlabs_agent",
                    model=self.advisor.model_name,
                ),
            )
        )
        facts = self._conversation_facts(spec.facts)
        self._job_facts_by_call[call.call_id] = facts
        context = await self.orchestrator.start_call(call.call_id)
        try:
            signed_url = await self.connection_adapter.create_connection()
        except Exception:
            self.orchestrator.handle_agent_event(
                call.call_id, AgentEvent(type=AgentEventType.CONNECTION_FAILED)
            )
            raise
        self.orchestrator.handle_agent_event(
            call.call_id, AgentEvent(type=AgentEventType.CONNECTION_ESTABLISHED)
        )
        disclosure = (
            "Hi, I'm an AI assistant calling on behalf of a buyer to collect a "
            "quote. Is now a good time to discuss it?"
        )
        self._append_transcript(call.call_id, "agent", disclosure)
        provider_context = {
            "call_id": call.call_id,
            "spec_sha256": call.spec_sha256,
            "vendor_name": context.vendor.name,
            "disclosure": disclosure,
            "first_message": disclosure,
            "opening_objective": (
                "Confirm that the vendor can discuss a quote now, then present the "
                "confirmed job naturally."
            ),
            "confirmed_job_spec_json": json.dumps(facts, ensure_ascii=False),
            "approved_verified_leverage_json": json.dumps(
                self._approved_leverage(context.policy), ensure_ascii=False
            ),
            "buyer_expected_price_json": json.dumps(
                self._buyer_expected_price(facts, context.augmented_context),
                ensure_ascii=False,
            ),
            "non_authoritative_context_json": json.dumps(
                context.augmented_context, ensure_ascii=False
            )[:24_000],
        }
        return self._response(
            call.call_id,
            provider_connection_url=signed_url,
            provider_context=provider_context,
        )

    async def advise(
        self, call_id: str, request: CallerAgentAdviceRequest
    ) -> CallerAgentResponse:
        view = self.orchestrator.get_call_view(call_id)
        if view.outcome is not None:
            raise ConflictError("This call has already been finalized")
        if any(pending.call_id == call_id for pending in self._pending.values()):
            raise ConflictError(
                "The previous GPT advice must be recorded before another vendor turn"
            )
        vendor_text = request.vendor_text.strip()
        vendor_event = self._append_transcript(call_id, "vendor", vendor_text)
        evidence = EvidenceInput(
            transcript_event_id=vendor_event.event_id,
            timestamp_seconds=vendor_event.timestamp_seconds,
        )
        view = self.orchestrator.get_call_view(call_id)
        advice = await self.advisor.advise(
            view,
            vendor_text,
            self._job_facts_by_call.get(call_id, {}),
            request.conversation_history,
        )
        self.orchestrator.record_dialogue_action(
            call_id, advice.planned_action.value
        )
        outcome = await self._apply_advice(call_id, vendor_text, evidence, advice)
        advice_id = f"advice_{uuid4().hex}"
        self._pending[advice_id] = _PendingAdvice(call_id=call_id, outcome=outcome)
        return self._response(
            call_id,
            advice_id=advice_id,
            advice=advice,
            recommended_outcome=outcome,
        )

    async def record_utterance(
        self, call_id: str, request: CallerAgentUtteranceRequest
    ) -> CallerAgentResponse:
        view = self.orchestrator.get_call_view(call_id)
        if view.outcome is not None:
            return self._response(call_id)

        pending: _PendingAdvice | None = None
        if request.advice_id is not None:
            pending = self._pending.get(request.advice_id)
            if pending is None or pending.call_id != call_id:
                raise ValidationError("Unknown or already-consumed Caller advice_id")
        else:
            raise ValidationError(
                "Every ElevenLabs response after the opening must reference its advice_id"
            )

        self._append_transcript(call_id, "agent", request.spoken_text.strip())
        if request.advice_id is not None:
            self._pending.pop(request.advice_id, None)
        if pending is not None and pending.outcome is not None:
            await self.orchestrator.finalize_call(call_id, pending.outcome)
        return self._response(call_id)

    async def end(self, call_id: str) -> CallerAgentResponse:
        view = self.orchestrator.get_call_view(call_id)
        self._pending = {
            advice_id: pending
            for advice_id, pending in self._pending.items()
            if pending.call_id != call_id
        }
        if view.outcome is None:
            if not view.transcript:
                self._append_transcript(
                    call_id,
                    "system",
                    "ElevenLabs ended before a conversational turn was captured.",
                )
            await self.orchestrator.finalize_call(
                call_id, OutcomeType.INCOMPLETE_QUOTE
            )
        return self._response(call_id)

    async def _apply_advice(
        self,
        call_id: str,
        vendor_text: str,
        evidence: EvidenceInput,
        advice: BuyerTurnAdvice,
    ) -> OutcomeType | None:
        view = self.orchestrator.get_call_view(call_id)
        status = view.call.status

        if advice.intent == "decline":
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="decline",
                    key="vendor_decline_reason",
                    value=vendor_text,
                    evidence=evidence,
                ),
            )
            return OutcomeType.DECLINED
        if advice.intent == "callback":
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="callback",
                    key="vendor_callback_commitment",
                    value=vendor_text,
                    evidence=evidence,
                ),
            )
            return OutcomeType.CALLBACK_REQUIRED
        if advice.intent == "end_incomplete":
            return OutcomeType.INCOMPLETE_QUOTE

        if status == CallStatus.DISCLOSURE:
            self._complete_phase(call_id, CallStatus.DISCLOSURE)
            self._complete_phase(call_id, CallStatus.JOB_PRESENTATION)

        self._persist_facts(call_id, advice, evidence)

        if advice.planned_action == DialogueAction.CLOSE_CALLBACK_REQUIRED:
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="callback",
                    key="missing_information_callback_required",
                    value=vendor_text,
                    evidence=evidence,
                ),
            )
            return OutcomeType.CALLBACK_REQUIRED

        current = self.orchestrator.get_call_view(call_id).call.status
        if (
            current == CallStatus.QUOTE_COLLECTION
            and advice.planned_action == DialogueAction.CLARIFY_FEES
        ):
            self._complete_phase(call_id, CallStatus.QUOTE_COLLECTION)
        elif (
            current == CallStatus.QUOTE_CLARIFICATION
            and advice.planned_action == DialogueAction.CONFIRM_SUMMARY
        ):
            self._complete_phase(call_id, CallStatus.QUOTE_CLARIFICATION)
        elif (
            current == CallStatus.SUMMARY_CONFIRMATION
            and advice.intent == "confirm_summary"
        ):
            quote = self.orchestrator.get_call_view(call_id).original_quote
            policy = self.orchestrator.get_call_view(call_id).call.policy
            if policy.require_itemization and not quote.line_items:
                return OutcomeType.INCOMPLETE_QUOTE
            return OutcomeType.COMPLETE_QUOTE
        return None

    def _persist_facts(
        self,
        call_id: str,
        advice: BuyerTurnAdvice,
        evidence: EvidenceInput,
    ) -> None:
        for item in advice.line_items:
            if item.amount is not None and item.amount < 0:
                continue
            self.orchestrator.log_quote_line_item(
                call_id,
                QuoteLineItemInput(
                    category=item.category,
                    description=item.description,
                    amount=item.amount,
                    quantity=item.quantity,
                    unit=item.unit,
                    unit_price=item.unit_price,
                    currency=item.currency,
                    evidence=evidence.model_copy(update={"source": item.evidence_source}),
                ),
            )
        for term in advice.terms:
            value = term.value_number if term.value_number is not None else term.value_text
            if value is None or (
                term.category == "estimated_total"
                and isinstance(value, float)
                and value < 0
            ):
                continue
            quote = self.orchestrator.get_call_view(call_id).original_quote
            if any(
                existing.category == term.category
                and existing.value == value
                and (
                    term.category in {"pricing_model", "estimated_total"}
                    or existing.key == term.key
                )
                for existing in quote.terms
            ):
                continue
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category=term.category,
                    key=term.key,
                    value=value,
                    evidence=evidence.model_copy(update={"source": term.evidence_source}),
                ),
            )

    def _complete_phase(self, call_id: str, phase: CallStatus) -> None:
        self.orchestrator.handle_agent_event(
            call_id,
            AgentEvent(type=AgentEventType.PHASE_COMPLETED, phase=phase),
        )

    def _append_transcript(self, call_id: str, speaker: str, text: str):
        view = self.orchestrator.get_call_view(call_id)
        sequence = max((event.sequence for event in view.transcript), default=-1) + 1
        started = view.call.started_at or datetime.now(UTC)
        elapsed = max(0.0, (datetime.now(UTC) - started).total_seconds())
        return self.orchestrator.handle_agent_event(
            call_id,
            AgentEvent(
                type=AgentEventType.TRANSCRIPT,
                transcript=TranscriptEventInput(
                    event_id=f"te_{uuid4().hex}",
                    speaker=speaker,
                    text=text,
                    timestamp_seconds=round(elapsed, 3),
                    sequence=sequence,
                ),
            ),
        )

    def _response(self, call_id: str, **updates: Any) -> CallerAgentResponse:
        view = self.orchestrator.get_call_view(call_id)
        return CallerAgentResponse(
            call=view,
            confirmed_job_facts=self._job_facts_by_call.get(call_id, {}),
            terminal=view.outcome is not None,
            model=f"ElevenLabs Agent + {self.advisor.model_name} adviser",
            **updates,
        )

    @staticmethod
    def _conversation_facts(facts: dict) -> dict:
        fields = facts.get("fields")
        if not isinstance(fields, dict):
            return dict(facts)
        return {
            name: field.get("value", "unknown") if isinstance(field, dict) else field
            for name, field in fields.items()
        }

    @staticmethod
    def _approved_leverage(policy: CallPolicy) -> dict | None:
        approved_id = policy.approved_leverage_bid_id
        for bid in policy.verified_competing_bids:
            if bid.bid_id == approved_id:
                return bid.model_dump(mode="json")
        return None

    @staticmethod
    def _buyer_expected_price(
        job_facts: dict[str, Any], augmented_context: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Prefer the immutable Estimator fact over optional research context."""
        value = job_facts.get("buyer_expected_price")
        if value is not None and value != "unknown":
            if isinstance(value, dict):
                return value
            return {
                "amount": value,
                "currency": "USD",
                "source": "confirmed_job_spec",
                "is_competing_bid": False,
            }
        fallback = augmented_context.get("buyer_expected_price")
        return fallback if isinstance(fallback, dict) else None


def build_elevenlabs_caller_router(loop: ElevenLabsCallerLoop) -> APIRouter:
    router = APIRouter(prefix="/api/v1/demo/agent", tags=["caller-agent-demo"])

    @router.post("/sessions", response_model=CallerAgentResponse)
    async def start_session(
        request: CallerAgentStartRequest | None = None,
    ) -> CallerAgentResponse:
        return await loop.start(request)

    @router.post(
        "/sessions/{call_id}/advise", response_model=CallerAgentResponse
    )
    async def advise_turn(
        call_id: str, request: CallerAgentAdviceRequest
    ) -> CallerAgentResponse:
        return await loop.advise(call_id, request)

    @router.post(
        "/sessions/{call_id}/utterances", response_model=CallerAgentResponse
    )
    async def record_utterance(
        call_id: str, request: CallerAgentUtteranceRequest
    ) -> CallerAgentResponse:
        return await loop.record_utterance(call_id, request)

    @router.post("/sessions/{call_id}/end", response_model=CallerAgentResponse)
    async def end_session(call_id: str) -> CallerAgentResponse:
        return await loop.end(call_id)

    return router


def build_unconfigured_elevenlabs_caller_router(reason: str) -> APIRouter:
    router = APIRouter(prefix="/api/v1/demo/agent", tags=["caller-agent-demo"])

    @router.post("/sessions")
    async def unavailable() -> None:
        raise ExternalServiceError(reason)

    return router
