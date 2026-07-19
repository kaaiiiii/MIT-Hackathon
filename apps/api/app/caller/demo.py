from __future__ import annotations

import base64
from time import perf_counter
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .audio import AudioPipelineAdapter
from .dialogue_planner import DialogueAction
from .errors import ConflictError, ExternalServiceError, NotFoundError
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

TtsMode = Literal["inline", "stream"]


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
    audio_base64: str = ""
    audio_content_type: str = "audio/mpeg"
    audio_url: str | None = None
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
        self,
        request: DemoStartRequest | None = None,
        *,
        include_audio: bool = True,
    ) -> DemoVoiceTurnResponse:
        started = perf_counter()
        turn = await self.simulator.start(request)
        return await self._with_audio(
            turn,
            timings={"caller": self._elapsed_ms(started)},
            include_audio=include_audio,
        )

    async def receive_audio(
        self,
        call_id: str,
        audio: bytes,
        *,
        filename: str,
        media_type: str,
        include_audio: bool = True,
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
            include_audio=include_audio,
        )

    async def receive_text(
        self, call_id: str, text: str, *, include_audio: bool = True
    ) -> DemoVoiceTurnResponse:
        started = perf_counter()
        turn = await self.simulator.receive(call_id, text)
        return await self._with_audio(
            turn,
            transcription=text,
            timings={"gpt_and_caller": self._elapsed_ms(started)},
            include_audio=include_audio,
        )

    async def speech_response(self, call_id: str) -> Response:
        """Stream the latest agent message as speech so playback starts immediately."""
        view = self.simulator.orchestrator.get_call_view(call_id)
        text = next(
            (
                event.text
                for event in reversed(view.transcript)
                if event.speaker == "agent"
            ),
            None,
        )
        if text is None:
            raise NotFoundError("The call has no agent message to speak yet.")
        return StreamingResponse(
            self.audio_adapter.synthesize_stream(text),
            media_type="audio/mpeg",
            headers={"cache-control": "no-store", "accept-ranges": "none"},
        )

    async def _with_audio(
        self,
        turn: DemoTurnResponse,
        *,
        transcription: str | None = None,
        timings: dict[str, float] | None = None,
        include_audio: bool = True,
    ) -> DemoVoiceTurnResponse:
        if not include_audio:
            call_id = turn.call.call.call_id
            agent_turns = sum(
                1 for event in turn.call.transcript if event.speaker == "agent"
            )
            completed_timings = dict(timings or {})
            completed_timings["total"] = round(sum(completed_timings.values()), 1)
            return DemoVoiceTurnResponse(
                **turn.model_dump(),
                transcription=transcription,
                audio_url=(
                    f"/api/v1/demo/voice/sessions/{call_id}/speech"
                    f"?turn={agent_turns}"
                ),
                pipeline_timings_ms=completed_timings,
            )
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
    """Text/voice harness over the real Caller domain. Every spoken turn comes from the LLM."""

    def __init__(
        self,
        orchestrator: CallOrchestrator,
        turn_model: BuyerTurnModel,
    ) -> None:
        if turn_model is None:
            raise ValueError(
                "TextVoiceSimulator requires a BuyerTurnModel — the demo has no "
                "scripted fallback."
            )
        self.orchestrator = orchestrator
        self.turn_model = turn_model
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
        view = self.orchestrator.get_call_view(call.call_id)
        message = await self.turn_model.opening(view, self._job_facts(call.call_id))
        self._append_transcript(call.call_id, "agent", message)
        return self._response(call.call_id, message)

    async def receive(self, call_id: str, text: str) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        if view.outcome is not None:
            raise ConflictError(
                "This call has already been finalized. Start a new session to continue."
            )

        vendor_event = self._append_transcript(call_id, "vendor", text.strip())
        evidence = EvidenceInput(
            transcript_event_id=vendor_event.event_id,
            timestamp_seconds=vendor_event.timestamp_seconds,
        )
        return await self._receive_with_model(call_id, text.strip(), evidence)

    async def _receive_with_model(
        self, call_id: str, text: str, evidence: EvidenceInput
    ) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        decision = await self.turn_model.respond(
            view,
            text,
            self._job_facts(call_id),
        )
        self.orchestrator.record_dialogue_action(
            call_id,
            decision.planned_action.value,
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
            if decision.planned_action == DialogueAction.CLARIFY_FEES:
                self.orchestrator.handle_agent_event(
                    call_id,
                    AgentEvent(
                        type=AgentEventType.PHASE_COMPLETED,
                        phase=CallStatus.QUOTE_COLLECTION,
                    ),
                )
            return self._say(call_id, decision.spoken_response)

        if status == CallStatus.QUOTE_CLARIFICATION:
            if decision.planned_action == DialogueAction.CONFIRM_SUMMARY:
                self.orchestrator.handle_agent_event(
                    call_id,
                    AgentEvent(
                        type=AgentEventType.PHASE_COMPLETED,
                        phase=CallStatus.QUOTE_CLARIFICATION,
                    ),
                )
            return self._say(call_id, decision.spoken_response)

        if status == CallStatus.SUMMARY_CONFIRMATION:
            if decision.intent == "confirm_summary":
                return await self._close_confirmed_summary(
                    call_id,
                    decision.spoken_response,
                )
            return self._say(call_id, decision.spoken_response)

        return self._say(call_id, decision.spoken_response)

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
                    evidence=evidence.model_copy(
                        update={"source": item.evidence_source}
                    ),
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
                    evidence=evidence.model_copy(
                        update={"source": term.evidence_source}
                    ),
                ),
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

    async def _close_confirmed_summary(
        self,
        call_id: str,
        message: str,
    ) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        outcome = (
            OutcomeType.INCOMPLETE_QUOTE
            if view.call.policy.require_itemization
            and not view.original_quote.line_items
            else OutcomeType.COMPLETE_QUOTE
        )
        return await self._close(call_id, outcome, message)

    def _response(self, call_id: str, message: str) -> DemoTurnResponse:
        view = self.orchestrator.get_call_view(call_id)
        return DemoTurnResponse(
            agent_message=message,
            call=view,
            confirmed_job_facts=self._job_facts(call_id),
            terminal=view.outcome is not None,
            model=self.turn_model.model_name,
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

    pipeline = (
        VoiceTurnPipeline(simulator, audio_adapter)
        if audio_adapter is not None
        else None
    )

    def voice_pipeline() -> VoiceTurnPipeline:
        if pipeline is None:
            raise ExternalServiceError(
                "ElevenLabs voice is not configured. Set ELEVENLABS_API_KEY and "
                "ELEVENLABS_VOICE_ID, then restart the API."
            )
        return pipeline

    @router.post("/voice/sessions", response_model=DemoVoiceTurnResponse)
    async def start_voice_session(
        request: DemoStartRequest | None = None,
        tts: TtsMode = "inline",
    ) -> DemoVoiceTurnResponse:
        return await voice_pipeline().start(request, include_audio=tts == "inline")

    @router.post(
        "/voice/sessions/{call_id}/messages",
        response_model=DemoVoiceTurnResponse,
    )
    async def send_voice_message(
        call_id: str,
        audio: UploadFile = File(...),
        tts: TtsMode = "inline",
    ) -> DemoVoiceTurnResponse:
        content = await audio.read()
        if len(content) > 25 * 1024 * 1024:
            raise ExternalServiceError("The recording exceeds the 25 MB demo limit.")
        return await voice_pipeline().receive_audio(
            call_id,
            content,
            filename=audio.filename or "vendor-recording.webm",
            media_type=audio.content_type or "application/octet-stream",
            include_audio=tts == "inline",
        )

    @router.post(
        "/voice/sessions/{call_id}/text",
        response_model=DemoVoiceTurnResponse,
    )
    async def send_voice_text(
        call_id: str,
        request: DemoMessageRequest,
        tts: TtsMode = "inline",
    ) -> DemoVoiceTurnResponse:
        return await voice_pipeline().receive_text(
            call_id, request.text, include_audio=tts == "inline"
        )

    @router.get("/voice/sessions/{call_id}/speech")
    async def stream_voice_speech(call_id: str, turn: int | None = None) -> Response:
        return await voice_pipeline().speech_response(call_id)

    return router
