from fastapi import APIRouter, Depends, status

from .orchestrator import CallOrchestrator
from .schemas import (
    AgentEvent,
    CallContext,
    CallCreateRequest,
    CallRecord,
    CallView,
    FinalizeCallRequest,
    StructuredCallOutcome,
    TranscriptEvent,
)


def build_caller_router(orchestrator: CallOrchestrator) -> APIRouter:
    router = APIRouter(prefix="/api/v1/calls", tags=["caller"])

    def get_orchestrator() -> CallOrchestrator:
        return orchestrator

    @router.post("", response_model=CallRecord, status_code=status.HTTP_201_CREATED)
    def create_call(
        request: CallCreateRequest,
        service: CallOrchestrator = Depends(get_orchestrator),
    ) -> CallRecord:
        return service.create_call(request)

    @router.post("/{call_id}/start", response_model=CallContext)
    async def start_call(
        call_id: str, service: CallOrchestrator = Depends(get_orchestrator)
    ) -> CallContext:
        return await service.start_call(call_id)

    @router.post("/{call_id}/events", response_model=TranscriptEvent | CallRecord)
    def post_event(
        call_id: str,
        event: AgentEvent,
        service: CallOrchestrator = Depends(get_orchestrator),
    ) -> TranscriptEvent | CallRecord:
        return service.handle_agent_event(call_id, event)

    @router.post("/{call_id}/finalize", response_model=StructuredCallOutcome)
    async def finalize_call(
        call_id: str,
        request: FinalizeCallRequest | None = None,
        service: CallOrchestrator = Depends(get_orchestrator),
    ) -> StructuredCallOutcome:
        return await service.finalize_call(
            call_id, request.requested_outcome if request else None
        )

    @router.get("/{call_id}", response_model=CallView)
    def get_call(
        call_id: str, service: CallOrchestrator = Depends(get_orchestrator)
    ) -> CallView:
        return service.get_call_view(call_id)

    return router

