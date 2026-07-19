from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ResearchStage(StrEnum):
    ESTIMATOR_ENRICHMENT = "estimator_enrichment"
    FINAL_REPORT_RESEARCH = "final_report_research"


class ResearchSource(BaseModel):
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    publisher: str | None = None


class ResearchClaim(BaseModel):
    claim: str = Field(min_length=1)
    relevance: str = Field(min_length=1)
    source_urls: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"


class ConversationOpportunity(BaseModel):
    """A safe way to use research during a call, never a fabricated quote."""

    objective: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    allowed_use: Literal["ask_vendor", "frame_confirmed_fact"]
    confirmed_field_names: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class ResearchArtifact(BaseModel):
    """Research, not confirmed customer fact or authorized leverage."""

    topic_summary: str = Field(min_length=1)
    terminology: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    assumptions_to_verify: list[str] = Field(default_factory=list)
    suggested_vendor_questions: list[str] = Field(default_factory=list)
    likely_fee_categories: list[str] = Field(default_factory=list)
    conversation_opportunities: list[ConversationOpportunity] = Field(
        default_factory=list
    )
    claims: list[ResearchClaim] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ResearchBundle(BaseModel):
    research_id: str
    stage: ResearchStage
    intake_session_id: str | None = None
    job_spec_version_id: str | None = None
    based_on_call_ids: list[str] = Field(default_factory=list)
    model: str
    artifact: ResearchArtifact
    provider_response_id: str | None = None
    created_at: datetime


class FinalResearchRequest(BaseModel):
    call_ids: list[str] | None = None


class ReportContext(BaseModel):
    job_spec_version_id: str
    confirmed_job_spec: dict
    estimator_research: ResearchBundle | None = None
    completed_calls: list[dict] = Field(default_factory=list)
    final_research: ResearchBundle | None = None
