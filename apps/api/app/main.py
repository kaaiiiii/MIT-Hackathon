from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .caller.adapters.simulated import SimulatedVoiceSessionAdapter
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
from .caller.input_gateway import InMemoryCallerInputGateway
from .caller.openai_agent import BuyerTurnModel, OpenAIBuyerTurnModel
from .caller.orchestrator import CallOrchestrator
from .caller.persistence import SQLiteCallerStore
from .caller.ports import CallerInputGateway, VoiceSessionAdapter
from .caller.router import build_caller_router


def create_app(
    *,
    database_path: str | None = None,
    inputs: CallerInputGateway | None = None,
    adapter: VoiceSessionAdapter | None = None,
    buyer_model: BuyerTurnModel | None = None,
) -> FastAPI:
    store = SQLiteCallerStore(
        database_path or os.getenv("CALLER_DB_PATH", "caller.sqlite3")
    )
    orchestrator = CallOrchestrator(
        store=store,
        inputs=inputs or InMemoryCallerInputGateway(),
        adapter=adapter or SimulatedVoiceSessionAdapter(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()

    app = FastAPI(title="Nego Caller API", version="0.1.0", lifespan=lifespan)
    app.state.caller_store = store
    app.state.call_orchestrator = orchestrator
    app.include_router(build_caller_router(orchestrator))

    demo_inputs = InMemoryCallerInputGateway([DEMO_SPEC], [DEMO_VENDOR])
    demo_orchestrator = CallOrchestrator(
        store=store,
        inputs=demo_inputs,
        adapter=SimulatedVoiceSessionAdapter(),
    )
    app.state.demo_orchestrator = demo_orchestrator
    app.include_router(
        build_demo_router(TextVoiceSimulator(demo_orchestrator, buyer_model))
    )
    app.state.demo_buyer_model = buyer_model

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

    return app


def _configured_buyer_model() -> BuyerTurnModel | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAIBuyerTurnModel(
        api_key=api_key,
        model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
        job_facts=DEMO_SPEC.facts,
    )


app = create_app(buyer_model=_configured_buyer_model())
