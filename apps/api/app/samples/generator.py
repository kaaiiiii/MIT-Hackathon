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


class AnalyzedQuote(BaseModel):
    agency_name: str = Field(min_length=1, max_length=80)
    quote_total: float | None = None
    currency: str = "USD"
    pricing_model: str | None = None
    fees: str | None = None
    binding_status: str | None = None
    availability: str | None = None
    red_flags: list[str] = Field(default_factory=list)


class SampleAnalysis(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    lowest_quote_agency: str | None = None
    quotes: list[AnalyzedQuote]
    observations: list[str] = Field(default_factory=list)


class SampleQuoteGenerator(Protocol):
    model_name: str

    async def generate(
        self,
        *,
        count: int,
        recorded_transcripts: list[str],
    ) -> list[SyntheticSample]: ...

    async def analyze(
        self,
        *,
        quotes: list[dict],
    ) -> SampleAnalysis: ...


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


_ANALYSIS_INSTRUCTIONS = """You analyze a set of sample vendor quotes collected \
for prototyping a quote-comparison product. Each input quote has an agency name, \
a transcript of the call, a source (recorded or synthetic), and a stated total \
when detected.

Rules:
- For each quote, extract only what the transcript states: total, pricing model \
(flat or hourly), fees, binding status, availability, and any red flags \
(vagueness, hidden-fee language, non-binding totals presented as firm).
- Leave a field null when the transcript does not state it; never invent values.
- summary: two to four plain sentences comparing the quotes for a buyer.
- lowest_quote_agency: the agency with the lowest stated total, or null when no \
totals were stated.
- observations: three to six short, concrete comparative notes (spread between \
highest and lowest, which quotes are binding, missing information worth asking \
about). These are prototype samples, not real vendors, so keep the tone factual.
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

    async def analyze(self, *, quotes: list[dict]) -> SampleAnalysis:
        payload = {"sample_quotes": quotes}
        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": "none"},
                text={"verbosity": "low"},
                instructions=_ANALYSIS_INSTRUCTIONS,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=SampleAnalysis,
                max_output_tokens=4000,
                store=False,
            )
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not analyze the sample quotes. Check the API "
                "key, billing, and model permissions."
            ) from exc
        analysis = response.output_parsed
        if analysis is None:
            raise ExternalServiceError("OpenAI returned no analysis.")
        return analysis
