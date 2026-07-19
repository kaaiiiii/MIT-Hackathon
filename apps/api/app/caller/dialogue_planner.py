from __future__ import annotations

from enum import StrEnum


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
    REQUEST_CONCESSION = "request_concession"
    CONFIRM_SUMMARY = "confirm_summary"
    CONTINUE = "continue"
