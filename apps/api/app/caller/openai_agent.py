from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field

from .dialogue_planner import DialogueAction
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
    "itemization_status",
]


class ExtractedLineItem(BaseModel):
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    amount: float | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    currency: str = "USD"
    evidence_source: Literal["vendor_statement", "vendor_confirmation"] = (
        "vendor_statement"
    )


class ExtractedTerm(BaseModel):
    category: TermCategory
    key: str = Field(min_length=1)
    value_text: str | None = None
    value_number: float | None = None
    evidence_source: Literal["vendor_statement", "vendor_confirmation"] = (
        "vendor_statement"
    )


class VendorTurnAnalysis(BaseModel):
    understanding: str = Field(default="", max_length=600)
    response_relation: Literal[
        "answered",
        "partially_answered",
        "refused",
        "off_topic",
        "unclear",
    ] = "answered"
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


class SpokenTurn(BaseModel):
    spoken_response: str = Field(min_length=1, max_length=900)


class BuyerTurnDecision(VendorTurnAnalysis):
    spoken_response: str = Field(min_length=1, max_length=900)
    planned_action: DialogueAction


class BuyerTurnModel(Protocol):
    model_name: str

    async def opening(
        self,
        view: CallView,
        job_facts: dict | None = None,
    ) -> str: ...

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
        self.adaptive_instructions = (
            f"{self.communication_policy.strip()}\n\n"
            f"{(prompt_directory / 'adaptive_turn_agent.txt').read_text(encoding='utf-8').strip()}\n"
        )

    async def respond(
        self,
        view: CallView,
        vendor_text: str,
        job_facts: dict | None = None,
    ) -> BuyerTurnDecision:
        active_job_facts = job_facts if job_facts is not None else self.job_facts
        candidate_actions = self._candidate_actions(view)
        payload = {
            "current_call_state": view.call.status.value,
            "confirmed_job_spec": self._job_spec(view, active_job_facts),
            "quote_so_far": view.original_quote.model_dump(mode="json"),
            "conversation_history": self._conversation_history(view),
            "full_transcript_event_count": len(view.transcript),
            "approved_verified_leverage": self._approved_leverage(view),
            "latest_vendor_statement": vendor_text,
            "dialogue_actions": view.dialogue_actions,
            "candidate_next_actions": [
                action.value for action in candidate_actions
            ],
            "non_authoritative_research_and_prior_calls": view.augmented_context,
        }
        try:
            decision = await self._request_decision(payload)
            for attempt in range(2):
                failure = self._validate_decision(decision, view)
                if failure is None:
                    return decision
                if attempt == 1:
                    raise ExternalServiceError(
                        "OpenAI failed buyer-turn validation twice: " + failure
                    )
                decision = await self._request_decision(
                    {
                        **payload,
                        "decision_validation_failure": failure,
                        "retry_instruction": (
                            "Rewrite the spoken response so it responds naturally to "
                            "the latest vendor turn. Keep planned_action, extracted "
                            "facts, and intent unless they conflict with the failure."
                        ),
                    }
                )
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not generate the buyer response. Check the API key, "
                "project access, billing, and model permissions."
            ) from exc

        raise ExternalServiceError("OpenAI returned no valid buyer turn")

    @staticmethod
    def _candidate_actions(view: CallView) -> list[DialogueAction]:
        """Phase-appropriate action menu; the LLM picks freely from it."""
        status = view.call.status.value
        blockers = [
            DialogueAction.REQUEST_PROVISIONAL_RANGE,
            DialogueAction.REQUEST_CALLBACK_REQUIREMENTS,
            DialogueAction.CLOSE_CALLBACK_REQUIRED,
        ]
        if status == "disclosure":
            return [DialogueAction.PRESENT_JOB, *blockers, DialogueAction.CONTINUE]
        if status == "quote_collection":
            return [
                DialogueAction.REQUEST_PRICING_MODEL,
                DialogueAction.REQUEST_ITEMIZATION,
                DialogueAction.REQUEST_TOTAL,
                DialogueAction.CLARIFY_FEES,
                *blockers,
                DialogueAction.CONTINUE,
            ]
        if status == "quote_clarification":
            actions = [
                DialogueAction.CLARIFY_FEES,
                DialogueAction.CLARIFY_BINDING,
                DialogueAction.CLARIFY_AVAILABILITY,
                DialogueAction.CONFIRM_SUMMARY,
            ]
            if view.call.policy.approved_leverage_bid_id:
                actions.insert(-1, DialogueAction.USE_VERIFIED_LEVERAGE)
            return [*actions, *blockers, DialogueAction.CONTINUE]
        if status == "summary_confirmation":
            return [
                DialogueAction.CONFIRM_SUMMARY,
                *blockers,
                DialogueAction.CONTINUE,
            ]
        return [DialogueAction.CONTINUE]

    async def opening(
        self,
        view: CallView,
        job_facts: dict | None = None,
    ) -> str:
        active_job_facts = job_facts if job_facts is not None else self.job_facts
        payload = {
            "current_call_state": view.call.status.value,
            "confirmed_job_spec": self._job_spec(view, active_job_facts),
            "quote_so_far": view.original_quote.model_dump(mode="json"),
            "conversation_history": self._conversation_history(view),
            "full_transcript_event_count": len(view.transcript),
            "approved_verified_leverage": self._approved_leverage(view),
            "dialogue_actions": view.dialogue_actions,
            "opening_turn": True,
            "spoken_intent": (
                "Open the call: disclose that you are an AI assistant calling on "
                "behalf of a buyer to request a quote, then ask whether now is a "
                "good time to discuss the confirmed job."
            ),
            "non_authoritative_research_and_prior_calls": view.augmented_context,
        }
        try:
            spoken_turn = await self._request_spoken(payload)
        except OpenAIError as exc:
            raise ExternalServiceError(
                "OpenAI could not generate the opening buyer response. Check the "
                "API key, project access, billing, and model permissions."
            ) from exc
        return spoken_turn.spoken_response

    async def _request_decision(self, payload: dict) -> BuyerTurnDecision:
        response = await self.client.responses.parse(
            model=self.model_name,
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
            instructions=self.adaptive_instructions,
            input=json.dumps(payload, ensure_ascii=False),
            text_format=BuyerTurnDecision,
            max_output_tokens=1200,
            store=False,
        )
        decision = response.output_parsed
        if decision is None:
            raise ExternalServiceError("OpenAI returned no buyer-turn decision")
        return decision

    def _validate_decision(
        self,
        decision: BuyerTurnDecision,
        view: CallView,
    ) -> str | None:
        """Guardrails on the spoken text only. Action choice is the LLM's call."""
        check = validate_spoken_response(
            decision.spoken_response,
            previous_buyer_turn(view.transcript),
            recent_buyer_turns(view.transcript),
            allow_question_repair=decision.response_relation
            in {"off_topic", "unclear"},
        )
        if not check.valid:
            return check.reason or "Spoken response failed validation"
        return None

    async def _request_spoken(self, payload: dict) -> SpokenTurn:
        response = await self.client.responses.parse(
            model=self.model_name,
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
            instructions=self.instructions,
            input=json.dumps(payload, ensure_ascii=False),
            text_format=SpokenTurn,
            max_output_tokens=300,
            store=False,
        )
        spoken = response.output_parsed
        if spoken is None:
            raise ExternalServiceError("OpenAI returned no spoken buyer response")
        return spoken

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
