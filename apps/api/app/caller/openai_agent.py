from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field

from .dialogue_planner import DialogueAction, DialoguePlan, DialoguePlanner
from .errors import ExternalServiceError
from .schemas import CallView
from .spoken_response import (
    previous_buyer_turn,
    recent_buyer_turns,
    validate_spoken_response,
)


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
    "vendor_requirement",
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
    planned_action: DialogueAction = DialogueAction.CONTINUE


class BuyerTurnModel(Protocol):
    model_name: str

    async def respond(
        self,
        view: CallView,
        vendor_text: str,
        job_facts: dict | None = None,
    ) -> BuyerTurnDecision: ...


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
        self.planner = DialoguePlanner()
        prompt_directory = Path(__file__).with_name("prompts")
        self.communication_policy = (
            prompt_directory / "negotiation_policy.txt"
        ).read_text(encoding="utf-8")
        self.surface_instructions = (
            prompt_directory / "text_buyer_agent.txt"
        ).read_text(encoding="utf-8")
        self.instructions = (
            f"{self.communication_policy.strip()}\n\n"
            f"{self.surface_instructions.strip()}\n"
        )

    async def respond(
        self,
        view: CallView,
        vendor_text: str,
        job_facts: dict | None = None,
    ) -> BuyerTurnDecision:
        active_job_facts = job_facts if job_facts is not None else self.job_facts
        plan = self.planner.plan(view, vendor_text, active_job_facts)
        payload = {
            "current_call_state": view.call.status.value,
            "confirmed_job_spec": self._job_spec(view, active_job_facts),
            "quote_so_far": view.original_quote.model_dump(mode="json"),
            "conversation_history": self._conversation_history(view),
            "full_transcript_event_count": len(view.transcript),
            "approved_verified_leverage": self._approved_leverage(view),
            "latest_vendor_statement": vendor_text,
            "dialogue_plan": plan.model_dump(mode="json"),
        }
        try:
            decision = await self._request_decision(payload)
            previous = previous_buyer_turn(view.transcript)
            recent = recent_buyer_turns(view.transcript)
            check = validate_spoken_response(
                decision.spoken_response,
                previous,
                recent,
            )
            if not check.valid:
                retry_payload = {
                    **payload,
                    "response_validation_failure": check.reason,
                    "retry_instruction": (
                        "Rewrite only the spoken response. Keep the exact selected "
                        "action, intent, and extracted facts. Use at most 45 words "
                        "and no more than one question mark."
                    ),
                }
                retry = await self._request_decision(retry_payload)
                retry_check = validate_spoken_response(
                    retry.spoken_response,
                    previous,
                    recent,
                )
                spoken = (
                    retry.spoken_response
                    if retry_check.valid
                    else self._fallback_spoken(plan)
                )
                decision = decision.model_copy(update={"spoken_response": spoken})
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not generate the buyer response. Check the API key, "
                "project access, billing, and model permissions."
            ) from exc

        return decision.model_copy(update={"planned_action": plan.selected_action})

    async def _request_decision(self, payload: dict) -> BuyerTurnDecision:
        response = await self.client.responses.parse(
            model=self.model_name,
            reasoning={"effort": "none"},
            text={"verbosity": "low"},
            instructions=self.instructions,
            input=json.dumps(payload, ensure_ascii=False),
            text_format=BuyerTurnDecision,
            max_output_tokens=900,
            store=False,
        )
        decision = response.output_parsed
        if decision is None:
            raise ExternalServiceError("OpenAI returned no structured buyer response")
        return decision

    @staticmethod
    def _job_spec(view: CallView, job_facts: dict) -> dict:
        return {
            "job_spec_version_id": view.call.job_spec_version_id,
            "spec_sha256": view.call.spec_sha256,
            "facts": job_facts,
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
    def _fallback_spoken(plan: DialoguePlan) -> str:
        responses = {
            DialogueAction.PRESENT_JOB: "Could you give me a rough price range for the confirmed piano move?",
            DialogueAction.REQUEST_PRICING_MODEL: "Would that be a flat price or an hourly rate?",
            DialogueAction.REQUEST_ITEMIZATION: "What is the main cost in that estimate?",
            DialogueAction.REQUEST_TOTAL: "What total should the customer expect?",
            DialogueAction.CLARIFY_FEES: "What extra charges should the customer expect?",
            DialogueAction.CLARIFY_BINDING: "Is that total binding?",
            DialogueAction.CLARIFY_AVAILABILITY: "Are you available on August 1?",
            DialogueAction.REQUEST_PROVISIONAL_RANGE: (
                "We don't have those measurements yet. Could you give a provisional "
                "range for a standard upright?"
            ),
            DialogueAction.REQUEST_CALLBACK_REQUIREMENTS: (
                "What exact information should we send, and when could you return the quote?"
            ),
            DialogueAction.CLOSE_CALLBACK_REQUIRED: (
                "I'll record this as requiring a callback after those details are provided."
            ),
            DialogueAction.USE_VERIFIED_LEVERAGE: "Can you match the verified competing bid?",
            DialogueAction.CONFIRM_SUMMARY: "Is that summary accurate?",
            DialogueAction.CONTINUE: "What is the next step for getting a quote?",
        }
        return responses[plan.selected_action]
