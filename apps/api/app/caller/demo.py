from __future__ import annotations

import base64
import re
from time import perf_counter
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from .audio import AudioPipelineAdapter
from .dialogue_planner import DialogueAction, DialoguePlan, DialoguePlanner
from .errors import ExternalServiceError
from .orchestrator import CallOrchestrator
from .openai_agent import BuyerTurnDecision, BuyerTurnModel
from .schemas import (
    AgentEvent,
    AgentEventType,
    CallCreateRequest,
    CallPolicy,
    CallStatus,
    CallView,
    ConfirmedJobSpec,
    EvidenceInput,
    OutcomeType,
    QuoteLineItemInput,
    QuoteTermInput,
    TranscriptEventInput,
    VendorTarget,
    VerifiedCompetingBid,
)


DEMO_SPEC = ConfirmedJobSpec(
    version_id="demo_spec_piano",
    status="confirmed",
    facts={
        "service": "Move one upright piano",
        "origin": "First-floor room in Brooklyn, New York",
        "destination": "Second-floor room in Queens, New York",
        "stairs": "One flight at the destination",
        "requested_date": "August 1, 2026",
    },
)

DEMO_VENDOR = VendorTarget(
    vendor_id="demo_vendor",
    name="Your Test Vendor",
    phone=None,
    metadata={"purpose": "text-in/voice-out demo"},
)


class DemoMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DemoStartRequest(BaseModel):
    job_spec_version_id: str | None = None
    verified_competing_bid: VerifiedCompetingBid | None = None


class DemoTurnResponse(BaseModel):
    agent_message: str
    call: CallView
    confirmed_job_facts: dict = Field(default_factory=dict)
    terminal: bool
    model: str


class DemoVoiceTurnResponse(DemoTurnResponse):
    transcription: str | None = None
    audio_base64: str
    audio_content_type: str
    audio_provider: str = "elevenlabs"
    pipeline_timings_ms: dict[str, float] = Field(default_factory=dict)


class VoiceTurnPipeline:
    """Composes STT -> Caller/GPT -> TTS without leaking audio into the domain."""

    def __init__(
        self,
        simulator: "TextVoiceSimulator",
        audio_adapter: AudioPipelineAdapter,
    ) -> None:
        self.simulator = simulator
        self.audio_adapter = audio_adapter

    async def start(
        self, request: DemoStartRequest | None = None
    ) -> DemoVoiceTurnResponse:
        started = perf_counter()
        turn = await self.simulator.start(request)
        return await self._with_audio(
            turn,
            timings={"caller": self._elapsed_ms(started)},
        )

    async def receive_audio(
        self,
        call_id: str,
        audio: bytes,
        *,
        filename: str,
        media_type: str,
    ) -> DemoVoiceTurnResponse:
        started = perf_counter()
        transcription = await self.audio_adapter.transcribe(
            audio,
            filename=filename,
            media_type=media_type,
        )
        stt_ms = self._elapsed_ms(started)
        started = perf_counter()
        turn = await self.simulator.receive(call_id, transcription)
        caller_ms = self._elapsed_ms(started)
        return await self._with_audio(
            turn,
            transcription=transcription,
            timings={"speech_to_text": stt_ms, "gpt_and_caller": caller_ms},
        )

    async def receive_text(
        self, call_id: str, text: str
    ) -> DemoVoiceTurnResponse:
        started = perf_counter()
        turn = await self.simulator.receive(call_id, text)
        return await self._with_audio(
            turn,
            transcription=text,
            timings={"gpt_and_caller": self._elapsed_ms(started)},
        )

    async def _with_audio(
        self,
        turn: DemoTurnResponse,
        *,
        transcription: str | None = None,
        timings: dict[str, float] | None = None,
    ) -> DemoVoiceTurnResponse:
        started = perf_counter()
        speech = await self.audio_adapter.synthesize(turn.agent_message)
        completed_timings = dict(timings or {})
        completed_timings["text_to_speech"] = self._elapsed_ms(started)
        completed_timings["total"] = round(sum(completed_timings.values()), 1)
        return DemoVoiceTurnResponse(
            **turn.model_dump(),
            transcription=transcription,
            audio_base64=base64.b64encode(speech.content).decode("ascii"),
            audio_content_type=speech.media_type,
            pipeline_timings_ms=completed_timings,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((perf_counter() - started) * 1000, 1)


class TextVoiceSimulator:
    """Text/voice harness over the real Caller domain, with an optional LLM."""

    def __init__(
        self,
        orchestrator: CallOrchestrator,
        turn_model: BuyerTurnModel | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.turn_model = turn_model
        self.dialogue_planner = DialoguePlanner()
        self._job_facts_by_call: dict[str, dict] = {}

    async def start(
        self, request: DemoStartRequest | None = None
    ) -> DemoTurnResponse:
        bid = request.verified_competing_bid if request else None
        version_id = (
            request.job_spec_version_id
            if request and request.job_spec_version_id
            else DEMO_SPEC.version_id
        )
        spec = self.orchestrator.inputs.get_confirmed_job_spec(version_id)
        if spec is None:
            from .errors import ValidationError

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
                vendor_id=DEMO_VENDOR.vendor_id,
                call_type="text_voice_demo",
                policy=policy,
            )
        )
        self._job_facts_by_call[call.call_id] = self._conversation_facts(spec.facts)
        await self.orchestrator.start_call(call.call_id)
        self.orchestrator.handle_agent_event(
            call.call_id, AgentEvent(type=AgentEventType.CONNECTION_ESTABLISHED)
        )
        message = (
            "Hello. I’m an AI assistant calling on behalf of a buyer to request "
            "a moving quote. Is now a good time to discuss the job?"
        )
        self._append_transcript(call.call_id, "agent", message)
        return self._response(call.call_id, message)

    async def receive(self, call_id: str, text: str) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        if view.outcome is not None:
            return self._response(
                call_id,
                "This test call has already ended. Start a new call to continue.",
            )

        vendor_event = self._append_transcript(call_id, "vendor", text.strip())
        evidence = EvidenceInput(
            transcript_event_id=vendor_event.event_id,
            timestamp_seconds=vendor_event.timestamp_seconds,
        )
        if self.turn_model is not None:
            return await self._receive_with_model(call_id, text.strip(), evidence)

        lowered = text.casefold()

        if self._is_decline(lowered):
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="decline",
                    key="vendor_decline_reason",
                    value=text.strip(),
                    evidence=evidence,
                ),
            )
            return await self._close(
                call_id,
                OutcomeType.DECLINED,
                "Understood. Thank you for your time. I’ve recorded that you declined to quote.",
            )

        if self._is_callback(lowered):
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="callback",
                    key="vendor_callback_commitment",
                    value=text.strip(),
                    evidence=evidence,
                ),
            )
            return await self._close(
                call_id,
                OutcomeType.CALLBACK_REQUIRED,
                "Thank you. I’ve recorded the callback commitment and will end this call now.",
            )

        if self._wants_to_end(lowered):
            return await self._close(
                call_id,
                OutcomeType.INCOMPLETE_QUOTE,
                "No problem. I’ve saved the information as an incomplete quote. Thank you.",
            )

        if self.turn_model is None:
            refreshed = self.orchestrator.get_call_view(call_id)
            plan = self.dialogue_planner.plan(
                refreshed, text.strip(), self._job_facts(call_id)
            )
            blocker_response = await self._handle_blocker_plan(
                call_id, plan, evidence
            )
            if blocker_response is not None:
                return blocker_response

        status = view.call.status
        if status == CallStatus.DISCLOSURE:
            return self._present_job(call_id)

        if status in {
            CallStatus.QUOTE_COLLECTION,
            CallStatus.QUOTE_CLARIFICATION,
            CallStatus.SUMMARY_CONFIRMATION,
        }:
            self._capture_quote_facts(call_id, text, evidence)

        if status == CallStatus.QUOTE_COLLECTION:
            return self._continue_collection(call_id)
        if status == CallStatus.QUOTE_CLARIFICATION:
            return self._continue_clarification(call_id)
        if status == CallStatus.SUMMARY_CONFIRMATION:
            if self._is_confirmation(lowered):
                return await self._close(
                    call_id,
                    OutcomeType.COMPLETE_QUOTE,
                    "Thank you. I’ve finalized the quote with its transcript evidence. "
                    "This does not accept a contract or commit the buyer.",
                )
            return self._say(
                call_id,
                "I recorded that correction. Is the revised summary now accurate?",
            )

        return self._say(call_id, "Could you clarify that for me?")

    async def _receive_with_model(
        self, call_id: str, text: str, evidence: EvidenceInput
    ) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        decision = await self.turn_model.respond(
            view,
            text,
            self._job_facts(call_id),
        )
        status = view.call.status

        if decision.intent == "decline":
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="decline",
                    key="vendor_decline_reason",
                    value=text,
                    evidence=evidence,
                ),
            )
            return await self._close(
                call_id, OutcomeType.DECLINED, decision.spoken_response
            )

        if decision.intent == "callback":
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="callback",
                    key="vendor_callback_commitment",
                    value=text,
                    evidence=evidence,
                ),
            )
            return await self._close(
                call_id, OutcomeType.CALLBACK_REQUIRED, decision.spoken_response
            )

        if decision.intent == "end_incomplete":
            return await self._close(
                call_id, OutcomeType.INCOMPLETE_QUOTE, decision.spoken_response
            )

        if status == CallStatus.DISCLOSURE:
            self.orchestrator.handle_agent_event(
                call_id,
                AgentEvent(
                    type=AgentEventType.PHASE_COMPLETED,
                    phase=CallStatus.DISCLOSURE,
                ),
            )
            self._append_transcript(call_id, "agent", decision.spoken_response)
            self.orchestrator.handle_agent_event(
                call_id,
                AgentEvent(
                    type=AgentEventType.PHASE_COMPLETED,
                    phase=CallStatus.JOB_PRESENTATION,
                ),
            )
            self._persist_model_facts(call_id, decision, evidence)
            return self._response(call_id, decision.spoken_response)

        self._persist_model_facts(call_id, decision, evidence)

        if decision.planned_action == DialogueAction.CLOSE_CALLBACK_REQUIRED:
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="callback",
                    key="missing_information_callback_required",
                    value=text,
                    evidence=evidence,
                ),
            )
            return await self._close(
                call_id, OutcomeType.CALLBACK_REQUIRED, decision.spoken_response
            )

        if status == CallStatus.QUOTE_COLLECTION:
            quote = self.orchestrator.get_call_view(call_id).original_quote
            categories = {term.category for term in quote.terms}
            if (
                "pricing_model" in categories
                and quote.line_items
                and "estimated_total" in categories
            ):
                self.orchestrator.handle_agent_event(
                    call_id,
                    AgentEvent(
                        type=AgentEventType.PHASE_COMPLETED,
                        phase=CallStatus.QUOTE_COLLECTION,
                    ),
                )
            return self._say(call_id, decision.spoken_response)

        if status == CallStatus.QUOTE_CLARIFICATION:
            quote = self.orchestrator.get_call_view(call_id).original_quote
            categories = {term.category for term in quote.terms}
            if {"fee", "binding_status", "availability"}.issubset(categories):
                leverage = self._pending_approved_leverage(call_id)
                if leverage is not None:
                    return self._say(call_id, self._leverage_message(leverage))
                self.orchestrator.handle_agent_event(
                    call_id,
                    AgentEvent(
                        type=AgentEventType.PHASE_COMPLETED,
                        phase=CallStatus.QUOTE_CLARIFICATION,
                    ),
                )
                return self._say(call_id, self._summary(call_id))
            return self._say(call_id, decision.spoken_response)

        if status == CallStatus.SUMMARY_CONFIRMATION:
            if decision.intent == "confirm_summary":
                return await self._close(
                    call_id, OutcomeType.COMPLETE_QUOTE, decision.spoken_response
                )
            if decision.intent == "correct_summary":
                return self._say(call_id, self._summary(call_id))
            return self._say(call_id, decision.spoken_response)

        return self._say(call_id, decision.spoken_response)

    async def _handle_blocker_plan(
        self,
        call_id: str,
        plan: DialoguePlan,
        evidence: EvidenceInput,
    ) -> DemoTurnResponse | None:
        blocker_actions = {
            DialogueAction.REQUEST_PROVISIONAL_RANGE,
            DialogueAction.REQUEST_CALLBACK_REQUIREMENTS,
            DialogueAction.CLOSE_CALLBACK_REQUIRED,
        }
        if plan.selected_action not in blocker_actions:
            return None
        for requirement in plan.vendor_requirements:
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category="vendor_requirement",
                    key=requirement.replace(" ", "_"),
                    value=requirement,
                    evidence=evidence,
                ),
            )
        if plan.selected_action == DialogueAction.REQUEST_PROVISIONAL_RANGE:
            return self._say(
                call_id,
                "We don’t have those measurements yet. Could you give a provisional "
                "range for a standard upright?",
            )
        if plan.selected_action == DialogueAction.REQUEST_CALLBACK_REQUIREMENTS:
            return self._say(
                call_id,
                "What exact information should we send, and when could you return the quote?",
            )
        self.orchestrator.log_quote_term(
            call_id,
            QuoteTermInput(
                category="callback",
                key="missing_information_callback_required",
                value={
                    "requirements": plan.vendor_requirements,
                    "missing_customer_facts": plan.missing_customer_facts,
                },
                evidence=evidence,
            ),
        )
        return await self._close(
            call_id,
            OutcomeType.CALLBACK_REQUIRED,
            "I’ll record this as requiring a callback after those details are provided.",
        )

    def _persist_model_facts(
        self,
        call_id: str,
        decision: BuyerTurnDecision,
        evidence: EvidenceInput,
    ) -> None:
        for item in decision.line_items:
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
                    evidence=evidence,
                ),
            )
        for term in decision.terms:
            value = (
                term.value_number
                if term.value_number is not None
                else term.value_text
            )
            if value is None:
                continue
            if term.category == "estimated_total" and isinstance(value, float) and value < 0:
                continue
            self.orchestrator.log_quote_term(
                call_id,
                QuoteTermInput(
                    category=term.category,
                    key=term.key,
                    value=value,
                    evidence=evidence,
                ),
            )

    def _present_job(self, call_id: str) -> DemoTurnResponse:
        self.orchestrator.handle_agent_event(
            call_id,
            AgentEvent(
                type=AgentEventType.PHASE_COMPLETED, phase=CallStatus.DISCLOSURE
            ),
        )
        facts = self._job_facts(call_id)
        message = self._job_presentation(facts)
        self._append_transcript(call_id, "agent", message)
        self.orchestrator.handle_agent_event(
            call_id,
            AgentEvent(
                type=AgentEventType.PHASE_COMPLETED,
                phase=CallStatus.JOB_PRESENTATION,
            ),
        )
        return self._response(call_id, message)

    def _continue_collection(self, call_id: str) -> DemoTurnResponse:
        quote = self.orchestrator.get_call_view(call_id).original_quote
        categories = {term.category for term in quote.terms}
        if "pricing_model" not in categories:
            return self._say(
                call_id,
                "Is that a flat price or an hourly estimate, and what does it include?",
            )
        if not quote.line_items:
            return self._say(
                call_id,
                "Could you itemize the main labor, travel, material, or equipment costs?",
            )
        if "estimated_total" not in categories:
            return self._say(
                call_id,
                "What is the estimated total for the confirmed job, before any unknown extras?",
            )

        self.orchestrator.handle_agent_event(
            call_id,
            AgentEvent(
                type=AgentEventType.PHASE_COMPLETED,
                phase=CallStatus.QUOTE_COLLECTION,
            ),
        )
        return self._say(
            call_id,
            "Are there any additional fees, minimums, travel charges, taxes, or "
            "other exclusions? Please also say whether the total is binding.",
        )

    def _continue_clarification(self, call_id: str) -> DemoTurnResponse:
        quote = self.orchestrator.get_call_view(call_id).original_quote
        categories = {term.category for term in quote.terms}
        if "fee" not in categories:
            return self._say(
                call_id,
                "To make the quote clear, are there any additional or hidden fees?",
            )
        if "binding_status" not in categories:
            return self._say(
                call_id,
                "Is the stated total binding, or can it change after inspection?",
            )
        if "availability" not in categories:
            return self._say(
                call_id,
                "Are you available for the requested date, August 1, 2026?",
            )

        leverage = self._pending_approved_leverage(call_id)
        if leverage is not None:
            return self._say(call_id, self._leverage_message(leverage))

        self.orchestrator.handle_agent_event(
            call_id,
            AgentEvent(
                type=AgentEventType.PHASE_COMPLETED,
                phase=CallStatus.QUOTE_CLARIFICATION,
            ),
        )
        return self._say(call_id, self._summary(call_id))

    def _pending_approved_leverage(
        self, call_id: str
    ) -> VerifiedCompetingBid | None:
        view = self.orchestrator.get_call_view(call_id)
        approved_id = view.call.policy.approved_leverage_bid_id
        if approved_id is None:
            return None
        bid = next(
            (
                candidate
                for candidate in view.call.policy.verified_competing_bids
                if candidate.bid_id == approved_id
            ),
            None,
        )
        if bid is None:
            return None
        current_total = self._latest_term_value(
            view.original_quote, "estimated_total"
        )
        if not isinstance(current_total, (int, float)) or current_total <= bid.total:
            return None
        marker = self._leverage_marker(bid)
        if any(
            event.speaker == "agent" and marker in event.text
            for event in view.transcript
        ):
            return None
        return bid

    @staticmethod
    def _leverage_marker(bid: VerifiedCompetingBid) -> str:
        amount = (
            f"${bid.total:,.2f}"
            if bid.currency.upper() == "USD"
            else f"{bid.currency.upper()} {bid.total:,.2f}"
        )
        return f"verified competing quote for {amount}"

    def _leverage_message(self, bid: VerifiedCompetingBid) -> str:
        binding = (
            "binding " if bid.binding_status == "binding" else ""
        )
        return (
            f"I have a {binding}{self._leverage_marker(bid)} for the same confirmed "
            "job. Can you match or beat that total, or improve the terms?"
        )

    def _capture_quote_facts(
        self, call_id: str, text: str, evidence: EvidenceInput
    ) -> None:
        lowered = text.casefold()
        pricing_model = None
        if re.search(r"\b(flat|fixed)\b", lowered):
            pricing_model = "flat"
        elif re.search(r"\b(hourly|per hour|an hour)\b", lowered):
            pricing_model = "hourly"
        if pricing_model:
            self._log_term(
                call_id, "pricing_model", "pricing_model", pricing_model, evidence
            )

        total = self._money_near(text, ("total", "altogether", "all-in", "all in"))
        if total is not None:
            self._log_term(call_id, "estimated_total", "total", total, evidence)

        item_patterns = {
            "labor": ("labor", "crew", "moving"),
            "travel": ("travel", "mileage", "trip"),
            "materials": ("material", "blanket", "packing"),
            "equipment": ("equipment", "piano board", "dolly"),
            "stairs": ("stairs", "flight"),
            "tax": ("tax",),
        }
        for category, labels in item_patterns.items():
            amount = self._money_near(text, labels)
            if amount is not None and amount != total:
                self.orchestrator.log_quote_line_item(
                    call_id,
                    QuoteLineItemInput(
                        category=category,
                        description=f"Vendor-stated {category} cost",
                        amount=amount,
                        evidence=evidence,
                    ),
                )

        if re.search(r"\b(no|without)\b.{0,35}\b(extra|additional|hidden)?\s*fees?\b", lowered):
            self._log_term(
                call_id, "fee", "additional_fees", "No additional fees stated", evidence
            )
        elif re.search(r"\b(fee|surcharge|minimum)\b", lowered):
            self._log_term(call_id, "fee", "fee_statement", text.strip(), evidence)

        if re.search(r"\b(non[- ]?binding|not binding|estimate only)\b", lowered):
            binding = "non-binding"
        elif re.search(r"\bbinding\b", lowered):
            binding = "binding"
        elif re.search(r"\bestimate(?:d)?\b", lowered):
            binding = "estimate"
        else:
            binding = None
        if binding:
            self._log_term(
                call_id, "binding_status", "binding_status", binding, evidence
            )

        if re.search(
            r"\b(available|availability|can do|can make|open on|book(?:ed| us)?)\b",
            lowered,
        ):
            self._log_term(
                call_id, "availability", "vendor_availability", text.strip(), evidence
            )

    def _summary(self, call_id: str) -> str:
        quote = self.orchestrator.get_call_view(call_id).original_quote
        total = self._latest_term_value(quote, "estimated_total")
        pricing = self._latest_term_value(quote, "pricing_model")
        binding = self._latest_term_value(quote, "binding_status")
        availability = self._latest_term_value(quote, "availability")
        items = ", ".join(
            f"{item.description}: ${item.amount:,.2f}"
            for item in quote.line_items
            if item.amount is not None
        )
        return (
            f"Let me read that back. The pricing model is {pricing}. "
            f"The itemized costs are {items}. The estimated total is "
            f"${float(total):,.2f}, with binding status {binding}. "
            f"You stated your availability as: {availability}. Is that accurate?"
        )

    def _say(self, call_id: str, message: str) -> DemoTurnResponse:
        self._append_transcript(call_id, "agent", message)
        return self._response(call_id, message)

    async def _close(
        self, call_id: str, outcome: OutcomeType, message: str
    ) -> DemoTurnResponse:
        self._append_transcript(call_id, "agent", message)
        await self.orchestrator.finalize_call(call_id, outcome)
        return self._response(call_id, message)

    def _response(self, call_id: str, message: str) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        return DemoTurnResponse(
            agent_message=message,
            call=view,
            confirmed_job_facts=self._job_facts(call_id),
            terminal=view.outcome is not None,
            model=self.turn_model.model_name if self.turn_model else "rule-based",
        )

    def _job_facts(self, call_id: str) -> dict:
        return self._job_facts_by_call.get(call_id, DEMO_SPEC.facts)

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
    def _job_presentation(facts: dict) -> str:
        service = str(facts.get("service", "the confirmed job")).strip()
        origin = facts.get("origin.location", facts.get("origin"))
        destination = facts.get("destination.location", facts.get("destination"))
        requested_date = facts.get("requested_date")
        details = [service]
        if origin:
            details.append(f"from {origin}")
        if destination:
            details.append(f"to {destination}")
        if requested_date:
            details.append(f"on {requested_date}")
        summary = ", ".join(str(item) for item in details)
        return f"The confirmed job is {summary}. How do you price this job?"

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

    def _log_term(
        self,
        call_id: str,
        category: str,
        key: str,
        value: object,
        evidence: EvidenceInput,
    ) -> None:
        self.orchestrator.log_quote_term(
            call_id,
            QuoteTermInput(
                category=category, key=key, value=value, evidence=evidence
            ),
        )

    @staticmethod
    def _latest_term_value(quote, category: str):
        values = [term.value for term in quote.terms if term.category == category]
        return values[-1] if values else "not established"

    @staticmethod
    def _money_near(text: str, labels: tuple[str, ...]) -> float | None:
        label_group = "|".join(re.escape(label) for label in labels)
        after = re.search(
            rf"(?:{label_group})\b[^$\d]{{0,25}}\$?(\d[\d,]*(?:\.\d{{1,2}})?)",
            text,
            re.IGNORECASE,
        )
        before = re.search(
            rf"\$(\d[\d,]*(?:\.\d{{1,2}})?)\s*(?:{label_group})\b",
            text,
            re.IGNORECASE,
        )
        match = after or before
        return float(match.group(1).replace(",", "")) if match else None

    @staticmethod
    def _is_decline(text: str) -> bool:
        return bool(
            re.search(
                r"\b(decline|not interested|cannot quote|can't quote|won't quote|do not quote)\b",
                text,
            )
        )

    @staticmethod
    def _is_callback(text: str) -> bool:
        return bool(
            re.search(
                r"\b(call back|callback|call you back|call you later|follow up later)\b",
                text,
            )
        )

    @staticmethod
    def _wants_to_end(text: str) -> bool:
        return bool(re.search(r"\b(end (the )?call|stop (the )?call|goodbye)\b", text))

    @staticmethod
    def _is_confirmation(text: str) -> bool:
        return bool(re.search(r"\b(yes|correct|accurate|confirmed|that's right|that is right)\b", text))


def build_demo_router(
    simulator: TextVoiceSimulator,
    audio_adapter: AudioPipelineAdapter | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/demo", tags=["caller-demo"])

    @router.post("/sessions", response_model=DemoTurnResponse)
    async def start_session(
        request: DemoStartRequest | None = None,
    ) -> DemoTurnResponse:
        return await simulator.start(request)

    @router.post("/sessions/{call_id}/messages", response_model=DemoTurnResponse)
    async def send_message(
        call_id: str, request: DemoMessageRequest
    ) -> DemoTurnResponse:
        return await simulator.receive(call_id, request.text)

    def voice_pipeline() -> VoiceTurnPipeline:
        if audio_adapter is None:
            raise ExternalServiceError(
                "ElevenLabs voice is not configured. Set ELEVENLABS_API_KEY and "
                "ELEVENLABS_VOICE_ID, then restart the API."
            )
        return VoiceTurnPipeline(simulator, audio_adapter)

    @router.post("/voice/sessions", response_model=DemoVoiceTurnResponse)
    async def start_voice_session(
        request: DemoStartRequest | None = None,
    ) -> DemoVoiceTurnResponse:
        return await voice_pipeline().start(request)

    @router.post(
        "/voice/sessions/{call_id}/messages",
        response_model=DemoVoiceTurnResponse,
    )
    async def send_voice_message(
        call_id: str,
        audio: UploadFile = File(...),
    ) -> DemoVoiceTurnResponse:
        content = await audio.read()
        if len(content) > 25 * 1024 * 1024:
            raise ExternalServiceError("The recording exceeds the 25 MB demo limit.")
        return await voice_pipeline().receive_audio(
            call_id,
            content,
            filename=audio.filename or "vendor-recording.webm",
            media_type=audio.content_type or "application/octet-stream",
        )

    @router.post(
        "/voice/sessions/{call_id}/text",
        response_model=DemoVoiceTurnResponse,
    )
    async def send_voice_text(
        call_id: str, request: DemoMessageRequest
    ) -> DemoVoiceTurnResponse:
        return await voice_pipeline().receive_text(call_id, request.text)

    return router
