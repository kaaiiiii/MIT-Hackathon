from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..caller.audio import AudioPipelineAdapter
from ..caller.errors import ExternalServiceError, NotFoundError, ValidationError
from .generator import SampleAnalysis, SampleQuoteGenerator
from .persistence import SQLiteSamplesStore


DEFAULT_QUOTES = 5
MAX_WEBSITE_QUOTES = 20
QUOTE_CAP_MESSAGE = (
    f"We can prepare up to {MAX_WEBSITE_QUOTES} sample quotes from the website. "
    "For more than that, please contact support."
)


class SampleSessionCreateRequest(BaseModel):
    target_quotes: int | None = Field(default=None, ge=1)


class SampleRecordingView(BaseModel):
    recording_id: str
    source: str
    agency_name: str | None
    transcript: str
    quote_total: float | None
    currency: str
    has_audio: bool
    audio_media_type: str | None


class SampleSessionView(BaseModel):
    session_id: str
    target_quotes: int
    recorded_count: int
    synthetic_count: int
    remaining_slots: int
    max_website_quotes: int = MAX_WEBSITE_QUOTES
    recordings: list[SampleRecordingView]
    analysis: SampleAnalysis | None = None


class SampleCallsService:
    def __init__(
        self,
        store: SQLiteSamplesStore,
        *,
        samples_dir: Path,
        audio_adapter: AudioPipelineAdapter | None = None,
        generator: SampleQuoteGenerator | None = None,
    ) -> None:
        self.store = store
        self.samples_dir = samples_dir
        self.audio_adapter = audio_adapter
        self.generator = generator

    def create_session(self, target_quotes: int | None) -> SampleSessionView:
        target = DEFAULT_QUOTES if target_quotes is None else target_quotes
        if target > MAX_WEBSITE_QUOTES:
            raise ValidationError(QUOTE_CAP_MESSAGE)
        session_id = f"samples_{uuid4().hex}"
        self.store.create_session(session_id, target)
        return self.view(session_id)

    def view(self, session_id: str) -> SampleSessionView:
        session = self.store.get_session(session_id)
        if session is None:
            raise NotFoundError("Unknown sample session")
        rows = self.store.recordings(session_id)
        recordings = [
            SampleRecordingView(
                recording_id=row["recording_id"],
                source=row["source"],
                agency_name=row["agency_name"],
                transcript=row["transcript"],
                quote_total=row["quote_total"],
                currency=row["currency"],
                has_audio=row["audio_path"] is not None,
                audio_media_type=row["media_type"],
            )
            for row in rows
        ]
        recorded = sum(1 for r in recordings if r.source == "recorded")
        synthetic = len(recordings) - recorded
        stored_analysis = self.store.get_analysis(session_id)
        return SampleSessionView(
            session_id=session_id,
            target_quotes=session["target_quotes"],
            recorded_count=recorded,
            synthetic_count=synthetic,
            remaining_slots=max(0, session["target_quotes"] - len(recordings)),
            recordings=recordings,
            analysis=(
                SampleAnalysis.model_validate_json(stored_analysis["analysis_json"])
                if stored_analysis is not None
                else None
            ),
        )

    async def add_recording(
        self,
        session_id: str,
        audio: bytes,
        *,
        filename: str,
        media_type: str,
        agency_name: str | None,
    ) -> SampleSessionView:
        view = self.view(session_id)
        if view.remaining_slots <= 0:
            raise ValidationError(
                "This session already has its target number of sample quotes."
            )
        if self.audio_adapter is None:
            raise ExternalServiceError(
                "ElevenLabs voice is not configured. Set ELEVENLABS_API_KEY and "
                "ELEVENLABS_VOICE_ID, then restart the API."
            )
        transcript = await self.audio_adapter.transcribe(
            audio, filename=filename, media_type=media_type
        )
        recording_id = f"rec_{uuid4().hex}"
        path = self._audio_path(session_id, recording_id, media_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
        self.store.add_recording(
            recording_id=recording_id,
            session_id=session_id,
            source="recorded",
            agency_name=agency_name,
            transcript=transcript,
            quote_total=self._stated_total(transcript),
            audio_path=str(path),
            media_type=media_type,
        )
        return self.view(session_id)

    async def synthesize_remaining(self, session_id: str) -> SampleSessionView:
        view = self.view(session_id)
        if view.recorded_count == 0:
            raise ValidationError(
                "Record at least one sample call first so the synthetic quotes "
                "can match your examples."
            )
        if view.remaining_slots <= 0:
            return view
        if self.generator is None:
            raise ExternalServiceError(
                "Synthetic generation needs OpenAI. Set OPENAI_API_KEY, then "
                "restart the API."
            )
        recorded_transcripts = [
            r.transcript for r in view.recordings if r.source == "recorded"
        ]
        samples = await self.generator.generate(
            count=view.remaining_slots,
            recorded_transcripts=recorded_transcripts,
        )
        for sample in samples:
            recording_id = f"syn_{uuid4().hex}"
            audio_path = None
            media_type = None
            if self.audio_adapter is not None:
                speech = await self.audio_adapter.synthesize(sample.transcript)
                path = self._audio_path(session_id, recording_id, speech.media_type)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(speech.content)
                audio_path = str(path)
                media_type = speech.media_type
            self.store.add_recording(
                recording_id=recording_id,
                session_id=session_id,
                source="synthetic",
                agency_name=sample.agency_name,
                transcript=sample.transcript,
                quote_total=sample.quote_total,
                currency=sample.currency,
                audio_path=audio_path,
                media_type=media_type,
            )
        return self.view(session_id)

    async def analyze(self, session_id: str) -> SampleSessionView:
        view = self.view(session_id)
        if not view.recordings:
            raise ValidationError(
                "Collect at least one sample quote before running the analysis."
            )
        if self.generator is None:
            raise ExternalServiceError(
                "Quote analysis needs OpenAI. Set OPENAI_API_KEY, then restart "
                "the API."
            )
        quotes = [
            {
                "agency_name": r.agency_name or f"Sample {index + 1}",
                "source": r.source,
                "stated_total": r.quote_total,
                "currency": r.currency,
                "transcript": r.transcript,
            }
            for index, r in enumerate(view.recordings)
        ]
        analysis = await self.generator.analyze(quotes=quotes)
        self.store.save_analysis(
            session_id,
            analysis.model_dump_json(),
            getattr(self.generator, "model_name", "unknown"),
        )
        return self.view(session_id)

    def audio_file(self, session_id: str, recording_id: str) -> tuple[Path, str]:
        row = self.store.get_recording(session_id, recording_id)
        if row is None or row["audio_path"] is None:
            raise NotFoundError("No audio stored for this sample recording")
        path = Path(row["audio_path"])
        if not path.is_file():
            raise NotFoundError("The stored audio file is missing from disk")
        return path, row["media_type"] or "application/octet-stream"

    def _audio_path(
        self, session_id: str, recording_id: str, media_type: str
    ) -> Path:
        extension = {
            "audio/webm": "webm",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/ogg": "ogg",
        }.get(media_type.split(";")[0].strip(), "bin")
        return self.samples_dir / session_id / f"{recording_id}.{extension}"

    @staticmethod
    def _stated_total(transcript: str) -> float | None:
        amounts = [
            float(match.replace(",", ""))
            for match in re.findall(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)", transcript)
        ]
        return max(amounts) if amounts else None


def build_samples_router(service: SampleCallsService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/samples", tags=["sample-calls"])

    @router.post("/sessions", response_model=SampleSessionView)
    async def create_session(
        request: SampleSessionCreateRequest | None = None,
    ) -> SampleSessionView:
        return service.create_session(request.target_quotes if request else None)

    @router.get("/sessions/{session_id}", response_model=SampleSessionView)
    async def get_session(session_id: str) -> SampleSessionView:
        return service.view(session_id)

    @router.post(
        "/sessions/{session_id}/recordings", response_model=SampleSessionView
    )
    async def add_recording(
        session_id: str,
        audio: UploadFile = File(...),
        agency_name: str | None = Form(default=None),
    ) -> SampleSessionView:
        content = await audio.read()
        if len(content) > 25 * 1024 * 1024:
            raise ExternalServiceError("The recording exceeds the 25 MB limit.")
        return await service.add_recording(
            session_id,
            content,
            filename=audio.filename or "sample-call.webm",
            media_type=audio.content_type or "application/octet-stream",
            agency_name=agency_name,
        )

    @router.post(
        "/sessions/{session_id}/synthesize", response_model=SampleSessionView
    )
    async def synthesize_remaining(session_id: str) -> SampleSessionView:
        return await service.synthesize_remaining(session_id)

    @router.post(
        "/sessions/{session_id}/analyze", response_model=SampleSessionView
    )
    async def analyze_session(session_id: str) -> SampleSessionView:
        return await service.analyze(session_id)

    @router.get("/sessions/{session_id}/recordings/{recording_id}/audio")
    async def recording_audio(session_id: str, recording_id: str) -> FileResponse:
        path, media_type = service.audio_file(session_id, recording_id)
        return FileResponse(path, media_type=media_type)

    return router
