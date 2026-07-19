from __future__ import annotations

from fastapi import APIRouter

from .schemas import FinalResearchRequest, ReportContext, ResearchBundle
from .service import ResearchService


def build_research_router(service: ResearchService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/research", tags=["research-context"])

    @router.post(
        "/intake/sessions/{session_id}/enrich", response_model=ResearchBundle
    )
    async def enrich_intake(session_id: str) -> ResearchBundle:
        return await service.enrich_intake(session_id)

    @router.post(
        "/specs/{version_id}/final", response_model=ResearchBundle
    )
    async def run_final_research(
        version_id: str, request: FinalResearchRequest | None = None
    ) -> ResearchBundle:
        return await service.final_research(
            version_id, request.call_ids if request else None
        )

    @router.get(
        "/specs/{version_id}/report-context", response_model=ReportContext
    )
    def get_report_context(version_id: str) -> ReportContext:
        return service.report_context(version_id)

    return router
