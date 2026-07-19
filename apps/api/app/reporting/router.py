from __future__ import annotations

from fastapi import APIRouter

from ..research.schemas import FinalResearchRequest, ReportContext
from ..research.service import ResearchService


def build_reporting_router(research: ResearchService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/reports", tags=["reporting"])

    @router.post("/{version_id}/prepare", response_model=ReportContext)
    async def prepare_report(
        version_id: str, request: FinalResearchRequest | None = None
    ) -> ReportContext:
        """Mandatory pre-report gate: all calls terminal, then fresh research."""
        await research.final_research(
            version_id, request.call_ids if request else None
        )
        return research.report_context(version_id)

    return router
