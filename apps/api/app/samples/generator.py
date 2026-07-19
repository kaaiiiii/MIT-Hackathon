from __future__ import annotations

import json
from typing import Protocol

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field

from ..caller.errors import ExternalServiceError


class SyntheticSample(BaseModel):
    agency_name: str = Field(min_length=1, max_length=80)
    quote_total: float = Field(gt=0)
    currency: str = "USD"
    transcript: str = Field(min_length=40, max_length=1600)


class SyntheticSampleBatch(BaseModel):
    samples: list[SyntheticSample]


class SampleQuoteGenerator(Protocol):
    model_name: str

    async def generate(
        self,
        *,
        count: int,
        recorded_transcripts: list[str],
    ) -> list[SyntheticSample]: ...


_INSTRUCTIONS = """You generate synthetic vendor-side sample calls for testing a \
quote-collection voice agent. The user recorded a few real examples of themselves \
role-playing moving agencies; produce additional distinct examples in the same \
spirit.

Rules:
- Each sample is one agency representative speaking on a phone call, giving a \
quote for a moving job (a realistic monologue of 60 to 120 words, first person, \
plain speakable prose, no markdown, no stage directions).
- Vary agency names, personalities, pricing models (flat vs hourly), totals, \
fees, binding status, and availability across samples. Do not reuse agency names \
or totals from the recorded examples.
- quote_total is the estimated total stated in the transcript; keep them \
consistent.
- These are synthetic test fixtures, not real businesses: never use the name of \
a real, well-known company.
"""


class OpenAISampleQuoteGenerator:
    """GPT-backed generator for synthetic agency sample calls."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.4",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model_name = model
        self.client = client or AsyncOpenAI(api_key=api_key)

    async def generate(
        self,
        *,
        count: int,
        recorded_transcripts: list[str],
    ) -> list[SyntheticSample]:
        payload = {
            "samples_to_generate": count,
            "recorded_examples": recorded_transcripts,
        }
        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": "none"},
                text={"verbosity": "low"},
                instructions=_INSTRUCTIONS,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=SyntheticSampleBatch,
                max_output_tokens=4000,
                store=False,
            )
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not generate synthetic sample calls. Check the API "
                "key, billing, and model permissions."
            ) from exc
        batch = response.output_parsed
        if batch is None or not batch.samples:
            raise ExternalServiceError(
                "OpenAI returned no synthetic sample calls."
            )
        return batch.samples[:count]
