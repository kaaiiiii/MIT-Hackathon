from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile, status

from .schemas import (
    CatalogResolveRequest,
    ConfirmSpecRequest,
    ConfirmedJobSpecView,
    DocumentParseResult,
    ElevenLabsConversationImportRequest,
    ElevenLabsConversationImportResult,
    IntakeSessionCreate,
    IntakeSessionView,
    PendingCatalogResolution,
    VoiceTurnRequest,
    VoiceTurnResponse,
    UnknownAcknowledgementRequest,
)
from .service import EstimatorService


def build_estimator_router(service: EstimatorService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/intake", tags=["estimator"])

    @router.post(
        "/sessions",
        response_model=IntakeSessionView,
        status_code=status.HTTP_201_CREATED,
    )
    def create_session(request: IntakeSessionCreate) -> IntakeSessionView:
        return service.create_session(request)

    @router.get("/sessions/{session_id}", response_model=IntakeSessionView)
    def get_session(session_id: str) -> IntakeSessionView:
        return service.get_session(session_id)

    @router.get("/specs")
    def list_confirmed_specs(limit: int = 20) -> list[dict]:
        return service.list_confirmed_specs(limit)

    @router.get("/specs/{version_id}", response_model=ConfirmedJobSpecView)
    def get_confirmed_spec(version_id: str) -> ConfirmedJobSpecView:
        return service.get_confirmed_spec(version_id)

    @router.post(
        "/sessions/{session_id}/voice",
        response_model=VoiceTurnResponse,
    )
    async def voice_turn(
        session_id: str, request: VoiceTurnRequest | None = None
    ) -> VoiceTurnResponse:
        if request is None:
            return await service.start_voice(session_id)
        return service.apply_voice_turn(session_id, request)

    @router.post(
        "/sessions/{session_id}/elevenlabs-import",
        response_model=ElevenLabsConversationImportResult,
    )
    async def import_elevenlabs_conversation(
        session_id: str, request: ElevenLabsConversationImportRequest
    ) -> ElevenLabsConversationImportResult:
        return await service.import_elevenlabs_conversation(
            session_id, request.conversation_id
        )

    @router.post(
        "/sessions/{session_id}/documents",
        response_model=DocumentParseResult,
    )
    async def upload_document(
        session_id: str,
        document_type: str = Form(...),
        document: UploadFile = File(...),
    ) -> DocumentParseResult:
        content = await document.read()
        if len(content) > 25 * 1024 * 1024:
            from .errors import EstimatorValidationError

            raise EstimatorValidationError("Document exceeds the 25 MB intake limit")
        return await service.apply_document(
            session_id,
            document_type=document_type,
            filename=document.filename or "intake-document",
            media_type=document.content_type or "application/octet-stream",
            content=content,
        )

    @router.post(
        "/sessions/{session_id}/resolve",
        response_model=PendingCatalogResolution | IntakeSessionView,
    )
    async def resolve_catalog(
        session_id: str, request: CatalogResolveRequest
    ) -> PendingCatalogResolution | IntakeSessionView:
        if request.resolution_id:
            return service.select_catalog_candidate(
                session_id,
                resolution_id=request.resolution_id,
                candidate_id=request.selected_candidate_id or "",
            )
        return await service.start_catalog_resolution(
            session_id,
            field_name=request.field_name or "",
            raw_statement=request.raw_user_statement or "",
            catalog_name=request.catalog_name or "",
        )

    @router.post(
        "/sessions/{session_id}/confirm",
        response_model=ConfirmedJobSpecView,
    )
    def confirm_spec(
        session_id: str, request: ConfirmSpecRequest
    ) -> ConfirmedJobSpecView:
        return service.confirm(session_id, request)

    @router.post(
        "/sessions/{session_id}/acknowledge-unknowns",
        response_model=IntakeSessionView,
    )
    def acknowledge_unknowns(
        session_id: str, request: UnknownAcknowledgementRequest
    ) -> IntakeSessionView:
        return service.acknowledge_unknowns(session_id, request)

    return router
