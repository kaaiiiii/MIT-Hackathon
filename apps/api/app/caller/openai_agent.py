from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field

from .errors import ExternalServiceError
from .schemas import CallView


TermCategory = Literal[
    "pricing_model",
    "fee",
    "discount",
    "term",
    "assumption",
    "exclusion",
    "red_flag",
    "availability",
    "binding_status",
    "estimated_total",
]


class ExtractedLineItem(BaseModel):
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    amount: float | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    currency: str = "USD"


class ExtractedTerm(BaseModel):
    category: TermCategory
    key: str = Field(min_length=1)
    value_text: str | None = None
    value_number: float | None = None


class BuyerTurnDecision(BaseModel):
    spoken_response: str = Field(min_length=1, max_length=900)
    intent: Literal[
        "continue",
        "callback",
        "decline",
        "end_incomplete",
        "confirm_summary",
        "correct_summary",
    ] = "continue"
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    terms: list[ExtractedTerm] = Field(default_factory=list)


class BuyerTurnModel(Protocol):
    model_name: str

    async def respond(self, view: CallView, vendor_text: str) -> BuyerTurnDecision: ...


class OpenAIBuyerTurnModel:
    """GPT conversation and same-turn extraction behind a typed boundary."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.4",
        job_facts: dict | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model_name = model
        self.job_facts = job_facts or {}
        self.client = client or AsyncOpenAI(api_key=api_key)
        prompt_path = Path(__file__).with_name("prompts") / "text_buyer_agent.txt"
        self.instructions = prompt_path.read_text(encoding="utf-8")

    async def respond(self, view: CallView, vendor_text: str) -> BuyerTurnDecision:
        payload = {
            "current_call_state": view.call.status.value,
            "confirmed_job_spec": self._job_spec(view),
            "quote_so_far": view.original_quote.model_dump(mode="json"),
            "conversation_history": self._conversation_history(view),
            "full_transcript_event_count": len(view.transcript),
            "approved_verified_leverage": self._approved_leverage(view),
            "latest_vendor_statement": vendor_text,
            "required_conversation_goal": self._goal(view),
        }
        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                reasoning={"effort": "none"},
                text={"verbosity": "low"},
                instructions=self.instructions,
                input=json.dumps(payload, ensure_ascii=False),
                text_format=BuyerTurnDecision,
                max_output_tokens=1200,
                store=False,
            )
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not generate the buyer response. Check the API key, "
                "project access, billing, and model permissions."
            ) from exc

        decision = response.output_parsed
        if decision is None:
            raise ExternalServiceError("OpenAI returned no structured buyer response")
        return decision

    def _job_spec(self, view: CallView) -> dict:
        return {
            "job_spec_version_id": view.call.job_spec_version_id,
            "spec_sha256": view.call.spec_sha256,
            "facts": self.job_facts,
        }

    @staticmethod
    def _conversation_history(
        view: CallView, max_characters: int = 24_000
    ) -> list[dict[str, str | int]]:
        """Keep the durable transcript complete in SQLite and send a bounded tail."""
        selected = []
        used = 0
        for event in reversed(view.transcript):
            cost = len(event.text) + 40
            if selected and used + cost > max_characters:
                break
            selected.append(
                {
                    "sequence": event.sequence,
                    "speaker": event.speaker,
                    "text": event.text,
                }
            )
            used += cost
        return list(reversed(selected))

    @staticmethod
    def _approved_leverage(view: CallView) -> dict | None:
        approved_id = view.call.policy.approved_leverage_bid_id
        if approved_id is None:
            return None
        for bid in view.call.policy.verified_competing_bids:
            if bid.bid_id == approved_id:
                return bid.model_dump(mode="json")
        return None

    @staticmethod
    def _goal(view: CallView) -> str:
        status = view.call.status.value
        quote = view.original_quote
        categories = {term.category for term in quote.terms}
        if status == "disclosure":
            return (
                "If the vendor agrees to continue, present the confirmed piano-moving "
                "job already stated by the buyer context and ask how the vendor prices it."
            )
        if status == "quote_collection":
            missing = []
            if "pricing_model" not in categories:
                missing.append("pricing model")
            if not quote.line_items:
                missing.append("itemized costs")
            if "estimated_total" not in categories:
                missing.append("estimated total")
            return "Ask naturally for the next missing quote field: " + ", ".join(missing)
        if status == "quote_clarification":
            missing = [
                label
                for category, label in (
                    ("fee", "additional or hidden fees"),
                    ("binding_status", "binding status"),
                    ("availability", "availability for August 1, 2026"),
                )
                if category not in categories
            ]
            if missing:
                return "Clarify the next missing term: " + ", ".join(missing)
            leverage = OpenAIBuyerTurnModel._approved_leverage(view)
            if leverage is not None:
                return (
                    "Respond to the vendor's answer. If the approved verified bid has "
                    "already been presented, clarify any revised price or term. "
                    "Otherwise, ask whether the vendor can match or beat its exact "
                    "terms without inventing additional leverage."
                )
            return "Prepare to confirm the evidence-backed quote summary."
        if status == "summary_confirmation":
            return "Determine whether the vendor confirms the readback or corrects it."
        return "Continue the quote conversation without changing the confirmed job."
