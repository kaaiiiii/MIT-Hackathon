from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .schemas import CallView


class DialogueAction(StrEnum):
    PRESENT_JOB = "present_job"
    REQUEST_PRICING_MODEL = "request_pricing_model"
    REQUEST_ITEMIZATION = "request_itemization"
    REQUEST_TOTAL = "request_total"
    CLARIFY_FEES = "clarify_fees"
    CLARIFY_BINDING = "clarify_binding"
    CLARIFY_AVAILABILITY = "clarify_availability"
    REQUEST_PROVISIONAL_RANGE = "request_provisional_range"
    REQUEST_CALLBACK_REQUIREMENTS = "request_callback_requirements"
    CLOSE_CALLBACK_REQUIRED = "close_callback_required"
    USE_VERIFIED_LEVERAGE = "use_verified_leverage"
    CONFIRM_SUMMARY = "confirm_summary"
    CONTINUE = "continue"


class DialoguePlan(BaseModel):
    conversation_state: str
    current_objective: str
    known_facts: list[str]
    missing_customer_facts: list[str]
    vendor_requirements: list[str]
    quote_progress: dict[str, Any]
    allowed_next_actions: list[DialogueAction]
    selected_action: DialogueAction
    spoken_intent: str
    blocker_turn: int = Field(default=0, ge=0, le=2)


_REQUIREMENTS: tuple[tuple[str, str], ...] = (
    ("dimensions", r"\b(dimension|dimensions|measurement|measurements|size)\b"),
    ("weight", r"\b(weight|weighs?|pounds?|lbs?)\b"),
    ("piano model", r"\b(model|make and model|serial number)\b"),
    ("exact pickup and delivery addresses", r"\b(exact |street )?address(?:es)?\b"),
    ("photos", r"\b(photo|photos|picture|pictures|images?)\b"),
)


class DialoguePlanner:
    """Selects one next action; it never writes conversational prose."""

    def plan(
        self,
        view: CallView,
        vendor_text: str,
        job_facts: dict[str, Any],
        current_turn_line_item_count: int = 0,
        current_turn_terms: dict[str, list[Any]] | None = None,
    ) -> DialoguePlan:
        current_turn_terms = current_turn_terms or {}
        known_facts = [f"{key}: {value}" for key, value in job_facts.items()]
        requirements = self._vendor_requirements(vendor_text)
        missing = self._missing_requirements(requirements, job_facts)
        progress = self._quote_progress(
            view,
            current_turn_line_item_count,
            current_turn_terms,
        )
        itemization_requested = self.itemization_was_requested(view)
        progress["itemization_requested"] = itemization_requested
        provisional_asked = self._agent_said(
            view,
            r"\b(provisional|rough (?:price |cost )?range|standard upright)\b",
        )
        requirements_asked = self._agent_said(
            view,
            r"\b(what exact (?:details|information)|what (?:details|information) should)\b",
        )
        blocker_continuation = bool(
            requirements
            and requirements_asked
            and progress["estimated_total"] is None
            and not self._contains_price(vendor_text)
        )

        if self._is_information_blocker(vendor_text, missing) or blocker_continuation:
            if not provisional_asked:
                return self._plan(
                    "resolve_blocker",
                    "determine_if_provisional_quote_is_possible",
                    known_facts,
                    missing,
                    requirements,
                    progress,
                    [
                        DialogueAction.REQUEST_PROVISIONAL_RANGE,
                        DialogueAction.REQUEST_CALLBACK_REQUIREMENTS,
                        DialogueAction.CLOSE_CALLBACK_REQUIRED,
                    ],
                    DialogueAction.REQUEST_PROVISIONAL_RANGE,
                    "Ask for a provisional range based on a standard upright and ask what could change it.",
                    1,
                )
            if not requirements_asked:
                return self._plan(
                    "resolve_blocker",
                    "capture_exact_callback_requirements",
                    known_facts,
                    missing,
                    requirements,
                    progress,
                    [
                        DialogueAction.REQUEST_CALLBACK_REQUIREMENTS,
                        DialogueAction.CLOSE_CALLBACK_REQUIRED,
                    ],
                    DialogueAction.REQUEST_CALLBACK_REQUIREMENTS,
                    "Ask what exact information is required and when a quote could be returned after receiving it.",
                    2,
                )
            return self._plan(
                "callback_required",
                "close_the_information_blocker",
                known_facts,
                missing,
                requirements,
                progress,
                [DialogueAction.CLOSE_CALLBACK_REQUIRED],
                DialogueAction.CLOSE_CALLBACK_REQUIRED,
                "State that the quote requires a callback after the missing information is supplied, then end clearly.",
                2,
            )

        status = view.call.status.value
        categories = {term.category for term in view.original_quote.terms} | set(
            current_turn_terms
        )
        has_line_items = bool(
            view.original_quote.line_items or current_turn_line_item_count
        )
        itemization_refused = any(
            term.category == "itemization_status" and term.value == "refused"
            for term in view.original_quote.terms
        ) or "refused" in current_turn_terms.get("itemization_status", [])
        if status == "disclosure":
            return self._simple(
                "job_presentation",
                "present_the_confirmed_job",
                DialogueAction.PRESENT_JOB,
                "Present the job once in plain language and ask for a rough price range.",
                known_facts,
                missing,
                requirements,
                progress,
            )
        if status == "quote_collection":
            allowed: list[DialogueAction] = []
            if "pricing_model" not in categories:
                allowed.append(DialogueAction.REQUEST_PRICING_MODEL)
            itemization_attempts = view.dialogue_actions.count(
                DialogueAction.REQUEST_ITEMIZATION.value
            )
            if (
                not has_line_items
                and not itemization_refused
                and itemization_attempts < 2
            ):
                allowed.append(DialogueAction.REQUEST_ITEMIZATION)
            if "estimated_total" not in categories:
                allowed.append(DialogueAction.REQUEST_TOTAL)
            else:
                allowed.append(DialogueAction.CLARIFY_FEES)

            if "pricing_model" not in categories:
                action = DialogueAction.REQUEST_PRICING_MODEL
                intent = "Ask whether the price is flat or hourly."
            elif (
                not has_line_items
                and not itemization_refused
                and not itemization_requested
            ):
                action = DialogueAction.REQUEST_ITEMIZATION
                intent = (
                    "Make the call's single itemization request: ask how the quoted "
                    "price breaks down into explicitly priced components."
                )
            elif "estimated_total" not in categories:
                action = DialogueAction.REQUEST_TOTAL
                intent = (
                    "Respect the vendor's itemization refusal without probing again, "
                    "then ask for the estimated total."
                    if itemization_refused
                    else (
                        "Itemization was already requested. Do not ask for it again; "
                        "ask for the estimated total."
                        if itemization_requested
                        and not has_line_items
                        else "Ask for the estimated total."
                    )
                )
            else:
                action = DialogueAction.CLARIFY_FEES
                intent = (
                    "Respect the vendor's itemization refusal without probing again, "
                    "then ask only whether the bundled total has additional fees."
                    if itemization_refused
                    else "Ask only whether the stated total has additional fees."
                )
            return self._simple(
                "quote_collection",
                action.value,
                action,
                intent,
                known_facts,
                missing,
                requirements,
                progress,
                allowed,
            )
        if status == "quote_clarification":
            allowed = []
            if "fee" not in categories:
                allowed.append(DialogueAction.CLARIFY_FEES)
            if "binding_status" not in categories:
                allowed.append(DialogueAction.CLARIFY_BINDING)
            if "availability" not in categories:
                allowed.append(DialogueAction.CLARIFY_AVAILABILITY)
            if not allowed:
                if (
                    view.call.policy.approved_leverage_bid_id
                    and DialogueAction.USE_VERIFIED_LEVERAGE.value
                    not in view.dialogue_actions
                ):
                    allowed.append(DialogueAction.USE_VERIFIED_LEVERAGE)
                else:
                    allowed.append(DialogueAction.CONFIRM_SUMMARY)

            if "fee" not in categories:
                action = DialogueAction.CLARIFY_FEES
                intent = "Ask only about extra charges or fees."
            elif "binding_status" not in categories:
                action = DialogueAction.CLARIFY_BINDING
                intent = "Ask whether the stated total is binding."
            elif "availability" not in categories:
                action = DialogueAction.CLARIFY_AVAILABILITY
                intent = "Ask whether the vendor is available on the requested date."
            elif (
                view.call.policy.approved_leverage_bid_id
                and DialogueAction.USE_VERIFIED_LEVERAGE.value
                not in view.dialogue_actions
            ):
                action = DialogueAction.USE_VERIFIED_LEVERAGE
                intent = "Use only the approved verified bid and ask for one concrete improvement."
            else:
                action = DialogueAction.CONFIRM_SUMMARY
                intent = "Move to a concise evidence-backed readback."
            return self._simple(
                "quote_clarification",
                action.value,
                action,
                intent,
                known_facts,
                missing,
                requirements,
                progress,
                allowed,
            )
        if status == "summary_confirmation":
            return self._simple(
                "summary_confirmation",
                "confirm_the_readback",
                DialogueAction.CONFIRM_SUMMARY,
                "Determine whether the vendor confirms or corrects the readback.",
                known_facts,
                missing,
                requirements,
                progress,
            )
        return self._simple(
            status,
            "continue_toward_a_structured_outcome",
            DialogueAction.CONTINUE,
            "Respond directly and move toward one valid structured outcome.",
            known_facts,
            missing,
            requirements,
            progress,
        )

    @staticmethod
    def _vendor_requirements(text: str) -> list[str]:
        return [
            label
            for label, pattern in _REQUIREMENTS
            if re.search(pattern, text, re.IGNORECASE)
        ]

    @staticmethod
    def _missing_requirements(
        requirements: list[str], job_facts: dict[str, Any]
    ) -> list[str]:
        facts = " ".join(
            f"{key} {value}" for key, value in job_facts.items()
        ).casefold()
        missing = []
        for requirement in requirements:
            if requirement == "dimensions" and not re.search(
                r"\b(dimension|measurement|\d+\s*(?:in|inch|cm|ft))", facts
            ):
                missing.append(requirement)
            elif requirement == "weight" and not re.search(
                r"\b(weight|\d+\s*(?:lb|pound|kg))", facts
            ):
                missing.append(requirement)
            elif requirement == "piano model" and not re.search(
                r"\b(model|serial|brand|make)\b", facts
            ):
                missing.append(requirement)
            elif requirement == "exact pickup and delivery addresses" and not re.search(
                r"\b\d{1,6}\s+[a-z].*\b(street|st|avenue|ave|road|rd|boulevard|blvd)\b",
                facts,
            ):
                missing.append(requirement)
            elif requirement == "photos" and "photo" not in facts and "image" not in facts:
                missing.append(requirement)
        return missing

    @staticmethod
    def _is_information_blocker(text: str, missing: list[str]) -> bool:
        if not missing:
            return False
        blocker = re.search(
            r"\b(need|require|must have|can't quote|cannot quote|unable to quote|"
            r"depends on|have to have|without (?:that|those|it))\b",
            text,
            re.IGNORECASE,
        )
        return bool(blocker and not DialoguePlanner._contains_price(text))

    @staticmethod
    def _contains_price(text: str) -> bool:
        return bool(
            re.search(
                r"\$\s*\d|\b\d+(?:\.\d+)?\s*dollars?\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _agent_said(view: CallView, pattern: str) -> bool:
        return any(
            event.speaker == "agent" and re.search(pattern, event.text, re.IGNORECASE)
            for event in view.transcript
        )

    @staticmethod
    def itemization_was_requested(view: CallView) -> bool:
        """Use persisted planner state; transcript matching supports older calls."""
        if DialogueAction.REQUEST_ITEMIZATION.value in view.dialogue_actions:
            return True
        return DialoguePlanner._agent_said(
            view,
            r"\b(?:itemi[sz]e|itemi[sz]ed|break(?:down|\s+down)|"
            r"price\s+include|costs?\s+(?:are\s+)?included|"
            r"priced?\s+(?:components?|parts?)\s+(?:are\s+)?included|"
            r"charges?\s+(?:make\s+up|are\s+in)|"
            r"labor.{0,40}(?:travel|material|equipment)|"
            r"(?:travel|material|equipment).{0,40}labor)\b",
        )

    @staticmethod
    def _quote_progress(
        view: CallView,
        current_turn_line_item_count: int = 0,
        current_turn_terms: dict[str, list[Any]] | None = None,
    ) -> dict[str, Any]:
        current_turn_terms = current_turn_terms or {}

        def values(category: str) -> list[Any]:
            stored = [
                term.value
                for term in view.original_quote.terms
                if term.category == category
            ]
            return stored + current_turn_terms.get(category, [])

        return {
            "pricing_model": (values("pricing_model") or [None])[-1],
            "itemization_status": (values("itemization_status") or [None])[-1],
            "line_item_count": (
                len(view.original_quote.line_items) + current_turn_line_item_count
            ),
            "fees": values("fee"),
            "estimated_total": (values("estimated_total") or [None])[-1],
            "binding_status": (values("binding_status") or [None])[-1],
            "availability": (values("availability") or [None])[-1],
        }

    def _simple(
        self,
        state: str,
        objective: str,
        action: DialogueAction,
        intent: str,
        known: list[str],
        missing: list[str],
        requirements: list[str],
        progress: dict[str, Any],
        allowed: list[DialogueAction] | None = None,
    ) -> DialoguePlan:
        return self._plan(
            state,
            objective,
            known,
            missing,
            requirements,
            progress,
            allowed or [action],
            action,
            intent,
            0,
        )

    @staticmethod
    def _plan(
        state: str,
        objective: str,
        known: list[str],
        missing: list[str],
        requirements: list[str],
        progress: dict[str, Any],
        allowed: list[DialogueAction],
        selected: DialogueAction,
        intent: str,
        blocker_turn: int,
    ) -> DialoguePlan:
        return DialoguePlan(
            conversation_state=state,
            current_objective=objective,
            known_facts=known,
            missing_customer_facts=missing,
            vendor_requirements=requirements,
            quote_progress=progress,
            allowed_next_actions=allowed,
            selected_action=selected,
            spoken_intent=intent,
            blocker_turn=blocker_turn,
        )
