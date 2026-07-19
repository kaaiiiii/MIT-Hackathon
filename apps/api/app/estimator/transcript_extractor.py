from __future__ import annotations

import json

from openai import AsyncOpenAI, OpenAIError

from ..caller.errors import ExternalServiceError
from .schemas import ElevenLabsConversationTranscript, TranscriptFieldExtraction
from .verticals import VerticalConfig


class OpenAITranscriptFieldExtractor:
    """Extracts only verbatim, user-spoken facts from a completed intake transcript."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.6-luna",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model_name = model
        self.client = client or AsyncOpenAI(api_key=api_key)

    async def extract_fields(
        self,
        *,
        config: VerticalConfig,
        transcript: ElevenLabsConversationTranscript,
    ) -> TranscriptFieldExtraction:
        user_turns = [
            turn.model_dump()
            for turn in transcript.turns
            if turn.role == "user"
        ]
        field_schema = {
            name: {
                "type": field.type,
                "required": field.required,
                "question": field.question,
            }
            for name, field in config.fields.items()
        }
        instructions = """
You recover structured Estimator fields from a completed voice-intake transcript.
Use only USER turns. Agent statements are questions, not evidence.

For each output field:
- field_name must be one of the supplied schema keys;
- turn_id must identify the single user turn that directly states the fact;
- value must be a short exact substring copied from that turn's message;
- output a field at most once.

Omit facts that are inferred, merely suggested by the agent, ambiguous, contradicted,
or absent. Never normalize, calculate, combine turns, fill defaults, or mark a value
unknown. User review and confirmation happen later.
""".strip()
        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": "medium"},
                text={"verbosity": "low"},
                instructions=instructions,
                input=json.dumps(
                    {"field_schema": field_schema, "user_turns": user_turns},
                    ensure_ascii=False,
                ),
                text_format=TranscriptFieldExtraction,
                max_output_tokens=3000,
                store=False,
            )
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not extract the completed ElevenLabs intake transcript"
            ) from exc
        if response.output_parsed is None:
            raise ExternalServiceError(
                "OpenAI returned no structured intake transcript extraction"
            )
        return response.output_parsed
