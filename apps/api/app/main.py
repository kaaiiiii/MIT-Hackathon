from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .caller.adapters.elevenlabs_audio import ElevenLabsAudioAdapter
from .caller.adapters.elevenlabs_agent import ElevenLabsCallerAgentAdapter
from .caller.adapters.simulated import SimulatedVoiceSessionAdapter
from .caller.audio import AudioPipelineAdapter
from .caller.errors import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)
from .caller.demo import (
    DEMO_SPEC,
    DEMO_VENDOR,
    TextVoiceSimulator,
    build_demo_router,
)
from .caller.elevenlabs_loop import (
    ElevenLabsCallerLoop,
    build_elevenlabs_caller_router,
    build_unconfigured_elevenlabs_caller_router,
)
from .caller.input_gateway import (
    EstimatorAwareCallerInputGateway,
    InMemoryCallerInputGateway,
)
from .caller.openai_agent import BuyerTurnModel, OpenAIBuyerTurnModel
from .caller.orchestrator import CallOrchestrator
from .caller.persistence import SQLiteCallerStore
from .caller.ports import CallerInputGateway, VoiceSessionAdapter
from .caller.router import build_caller_router
from .estimator.adapters import (
    ElevenLabsAgentsIntakeAdapter,
    InMemoryCatalogResolver,
    SimulatedIntakeVoiceAdapter,
    StructuredJsonDocumentParser,
)
from .estimator.errors import (
    EstimatorConflictError,
    EstimatorNotFoundError,
    EstimatorValidationError,
)
from .estimator.persistence import SQLiteEstimatorStore
from .estimator.ports import (
    CatalogResolver,
    ConversationTranscriptImporter,
    DocumentParser,
    IntakeVoiceAdapter,
    TranscriptFieldExtractor,
)
from .estimator.router import build_estimator_router
from .estimator.service import EstimatorService
from .estimator.transcript_extractor import OpenAITranscriptFieldExtractor
from .estimator.verticals import VerticalConfigLoader
from .research.openai_researcher import ContextResearcher, OpenAIContextResearcher
from .research.persistence import SQLiteResearchStore
from .research.router import build_research_router
from .research.service import CallerResearchContextProvider, ResearchService
from .reporting.router import build_reporting_router
from .samples.generator import OpenAISampleQuoteGenerator, SampleQuoteGenerator
from .samples.persistence import SQLiteSamplesStore
from .samples.router import SampleCallsService, build_samples_router


class SPAStaticFiles(StaticFiles):
    """Serve the Vite build while preserving client-side routes on refresh."""

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


def create_app(
    *,
    database_path: str | None = None,
    inputs: CallerInputGateway | None = None,
    adapter: VoiceSessionAdapter | None = None,
    buyer_model: BuyerTurnModel | None = None,
    caller_agent_adapter: ElevenLabsCallerAgentAdapter | None = None,
    audio_adapter: AudioPipelineAdapter | None = None,
    estimator_document_parser: DocumentParser | None = None,
    estimator_catalog_resolver: CatalogResolver | None = None,
    estimator_configs: VerticalConfigLoader | None = None,
    estimator_voice_adapter: IntakeVoiceAdapter | None = None,
    estimator_conversation_importer: ConversationTranscriptImporter | None = None,
    estimator_transcript_extractor: TranscriptFieldExtractor | None = None,
    context_researcher: ContextResearcher | None = None,
    sample_generator: SampleQuoteGenerator | None = None,
    samples_dir: str | None = None,
) -> FastAPI:
    store = SQLiteCallerStore(
        database_path or os.getenv("CALLER_DB_PATH", "caller.sqlite3")
    )
    configured_estimator_voice_adapter = (
        estimator_voice_adapter or _configured_intake_voice_adapter()
    )
    configured_caller_agent_adapter = (
        caller_agent_adapter or _configured_caller_agent_adapter()
    )
    configured_conversation_importer = estimator_conversation_importer
    if configured_conversation_importer is None and hasattr(
        configured_estimator_voice_adapter, "fetch_conversation"
    ):
        configured_conversation_importer = configured_estimator_voice_adapter  # type: ignore[assignment]
    upstream_inputs = inputs or InMemoryCallerInputGateway()
    composed_inputs = EstimatorAwareCallerInputGateway(
        store.connection, upstream_inputs
    )
    estimator_store = SQLiteEstimatorStore(store.connection)
    estimator_service = EstimatorService(
        store=estimator_store,
        configs=estimator_configs or VerticalConfigLoader(),
        document_parser=estimator_document_parser or StructuredJsonDocumentParser(),
        catalog_resolver=estimator_catalog_resolver or InMemoryCatalogResolver(),
        voice_adapter=configured_estimator_voice_adapter,
        conversation_importer=configured_conversation_importer,
        transcript_extractor=(
            estimator_transcript_extractor or _configured_transcript_extractor()
        ),
    )
    research_store = SQLiteResearchStore(store.connection)
    research_service = ResearchService(
        store=research_store,
        researcher=context_researcher,
        estimator_service=estimator_service,
    )
    context_provider = CallerResearchContextProvider(research_service)
    orchestrator = CallOrchestrator(
        store=store,
        inputs=composed_inputs,
        adapter=adapter or SimulatedVoiceSessionAdapter(),
        context_provider=context_provider,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if audio_adapter is not None:
            await audio_adapter.close()
        close_voice = getattr(configured_estimator_voice_adapter, "close", None)
        if close_voice is not None:
            await close_voice()
        if configured_caller_agent_adapter is not None:
            await configured_caller_agent_adapter.close()
        store.close()

    app = FastAPI(title="Nego Caller API", version="0.1.0", lifespan=lifespan)
    app.state.caller_store = store
    app.state.call_orchestrator = orchestrator
    app.include_router(build_caller_router(orchestrator))

    app.state.estimator_store = estimator_store
    app.state.estimator_service = estimator_service
    app.include_router(build_estimator_router(estimator_service))
    app.state.research_store = research_store
    app.state.research_service = research_service
    app.include_router(build_research_router(research_service))
    app.include_router(build_reporting_router(research_service))

    demo_inputs = EstimatorAwareCallerInputGateway(
        store.connection,
        InMemoryCallerInputGateway([DEMO_SPEC], [DEMO_VENDOR]),
    )
    demo_orchestrator = CallOrchestrator(
        store=store,
        inputs=demo_inputs,
        adapter=SimulatedVoiceSessionAdapter(),
        context_provider=context_provider,
    )
    app.state.demo_orchestrator = demo_orchestrator
    if buyer_model is not None:
        app.include_router(
            build_demo_router(
                TextVoiceSimulator(demo_orchestrator, buyer_model),
                audio_adapter,
            )
        )
    app.state.demo_buyer_model = buyer_model
    app.state.demo_audio_adapter = audio_adapter
    app.state.caller_agent_adapter = configured_caller_agent_adapter
    if (
        buyer_model is not None
        and hasattr(buyer_model, "advise")
        and configured_caller_agent_adapter is not None
    ):
        caller_agent_loop = ElevenLabsCallerLoop(
            orchestrator=demo_orchestrator,
            advisor=buyer_model,  # type: ignore[arg-type]
            connection_adapter=configured_caller_agent_adapter,
            default_spec_version_id=DEMO_SPEC.version_id,
            vendor=DEMO_VENDOR,
        )
        app.state.caller_agent_loop = caller_agent_loop
        app.include_router(build_elevenlabs_caller_router(caller_agent_loop))
    else:
        missing = []
        if configured_caller_agent_adapter is None:
            missing.append("ELEVENLABS_CALLER_AGENT_ID and ELEVENLABS_API_KEY")
        if buyer_model is None or not hasattr(buyer_model, "advise"):
            missing.append("OPENAI_API_KEY")
        app.include_router(
            build_unconfigured_elevenlabs_caller_router(
                "ElevenLabs Caller Agent is not configured. Set "
                + " and ".join(missing)
                + ", then restart the API."
            )
        )

    samples_service = SampleCallsService(
        SQLiteSamplesStore(store.connection),
        samples_dir=Path(
            samples_dir
            or os.getenv("SAMPLE_CALLS_DIR", "data/sample_calls")
        ),
        audio_adapter=audio_adapter,
        generator=sample_generator or _configured_sample_generator(),
    )
    app.state.samples_service = samples_service
    app.include_router(build_samples_router(samples_service))

    demo_directory = Path(__file__).resolve().parents[2] / "web" / "demo"
    app.mount("/demo", StaticFiles(directory=demo_directory, html=True), name="demo")

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "detail": str(exc)},
        )

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "conflict", "detail": str(exc)},
        )

    @app.exception_handler(ValidationError)
    async def invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "validation_error", "detail": str(exc)},
        )

    @app.exception_handler(ExternalServiceError)
    async def external_service(
        _: Request, exc: ExternalServiceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": "external_service_error", "detail": str(exc)},
        )

    @app.exception_handler(EstimatorNotFoundError)
    async def estimator_not_found(
        _: Request, exc: EstimatorNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "estimator_not_found", "detail": str(exc)},
        )

    @app.exception_handler(EstimatorConflictError)
    async def estimator_conflict(
        _: Request, exc: EstimatorConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": "estimator_conflict", "detail": str(exc)},
        )

    @app.exception_handler(EstimatorValidationError)
    async def estimator_invalid(
        _: Request, exc: EstimatorValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "estimator_validation_error", "detail": str(exc)},
        )

    frontend_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if frontend_dist.is_dir():
        app.mount(
            "/",
            SPAStaticFiles(directory=frontend_dist, html=True),
            name="negotiator-web",
        )

    return app


def _configured_buyer_model() -> BuyerTurnModel | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAIBuyerTurnModel(
        api_key=api_key,
        model=os.getenv("OPENAI_CALLER_MODEL", "gpt-5.4-mini"),
        job_facts=DEMO_SPEC.facts,
        adviser_reasoning_effort=os.getenv(
            "OPENAI_CALLER_REASONING_EFFORT", "none"
        ),
        api_timeout_seconds=float(os.getenv("OPENAI_CALLER_TIMEOUT_SECONDS", "8")),
    )


def _configured_audio_adapter() -> AudioPipelineAdapter | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        return None
    return ElevenLabsAudioAdapter(
        api_key=api_key,
        voice_id=voice_id,
        stt_model=os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2"),
        tts_model=os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
    )


def _configured_caller_agent_adapter() -> ElevenLabsCallerAgentAdapter | None:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    agent_id = os.getenv("ELEVENLABS_CALLER_AGENT_ID")
    if not api_key or not agent_id:
        return None
    return ElevenLabsCallerAgentAdapter(api_key=api_key, agent_id=agent_id)


def _configured_intake_voice_adapter() -> IntakeVoiceAdapter:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    agent_id = os.getenv("ELEVENLABS_INTAKE_AGENT_ID")
    if api_key and agent_id:
        return ElevenLabsAgentsIntakeAdapter(api_key=api_key, agent_id=agent_id)
    return SimulatedIntakeVoiceAdapter()


def _configured_sample_generator() -> SampleQuoteGenerator | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAISampleQuoteGenerator(
        api_key=api_key,
        model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
    )


def _configured_context_researcher() -> ContextResearcher | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAIContextResearcher(
        api_key=api_key,
        model=os.getenv("OPENAI_RESEARCH_MODEL", "gpt-5.6-terra"),
    )


def _configured_transcript_extractor() -> TranscriptFieldExtractor | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAITranscriptFieldExtractor(
        api_key=api_key,
        model=os.getenv(
            "OPENAI_ESTIMATOR_MODEL",
            os.getenv("OPENAI_RESEARCH_MODEL", "gpt-5.2"),
        ),
    )


app = create_app(
    buyer_model=_configured_buyer_model(),
    audio_adapter=_configured_audio_adapter(),
    context_researcher=_configured_context_researcher(),
)
