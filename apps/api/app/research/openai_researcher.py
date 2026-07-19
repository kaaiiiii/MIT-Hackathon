from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI, OpenAIError

from ..caller.errors import ExternalServiceError
from .schemas import ResearchArtifact


@dataclass(frozen=True)
class ResearchResult:
    artifact: ResearchArtifact
    response_id: str | None = None


class ContextResearcher(Protocol):
    model_name: str

    async def research(self, *, stage: str, payload: dict) -> ResearchResult: ...


class OpenAIContextResearcher:
    """Web-grounded GPT research with a typed result."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.6-terra",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model_name = model
        self.client = client or AsyncOpenAI(api_key=api_key)

    async def research(self, *, stage: str, payload: dict) -> ResearchResult:
        effort = "medium"
        instructions = """
You are the grounded research service for a buyer-side quoting system.
Search current, authoritative web sources for context relevant to the supplied job.
Identify relevant terminology, operational risks, hidden-fee categories,
assumptions a vendor must confirm, and concise open questions that could improve
later vendor calls. Produce conversation_opportunities that the Caller can use from
the beginning of the conversation. Each opportunity must say whether it is only a
question to ask or a framing of named, confirmed fields. For final-report research,
reconcile the completed-call record with current external context and identify
material comparison caveats.

Hard boundary: the confirmed specification is the only source of customer/job
facts. Research is contextual guidance, never a replacement for confirmed facts,
never a vendor quote, and never authorized competing-bid leverage. Do not invent
missing customer details, vendor prices, or benchmark values. Every external claim
must list the URL that supports it. External market prices may be researched for
context but must not be represented as a vendor offer, binding quote, or approved
leverage. State limitations and disagreements plainly.
""".strip()
        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": effort},
                text={"verbosity": "medium"},
                tools=[{"type": "web_search"}],
                instructions=instructions,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=ResearchArtifact,
                max_output_tokens=8000,
                store=False,
            )
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not complete grounded research. Check the API key, "
                "billing, model access, and web-search tool access."
            ) from exc
        if response.output_parsed is None:
            raise ExternalServiceError("OpenAI returned no structured research result")
        return ResearchResult(
            artifact=response.output_parsed,
            response_id=getattr(response, "id", None),
        )
