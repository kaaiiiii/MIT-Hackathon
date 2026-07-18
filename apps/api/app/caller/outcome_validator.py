from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError
from .schemas import CallPolicy, OutcomeType, QuoteDraft, TranscriptEvent


@dataclass(frozen=True)
class ValidatedOutcome:
    outcome_type: OutcomeType
    warnings: list[str]
    reason: str | None = None


class CallOutcomeValidator:
    def infer(self, quote: QuoteDraft) -> OutcomeType:
        categories = {term.category for term in quote.terms}
        if "decline" in categories:
            return OutcomeType.DECLINED
        if "callback" in categories:
            return OutcomeType.CALLBACK_REQUIRED
        if "estimated_total" in categories:
            return OutcomeType.COMPLETE_QUOTE
        return OutcomeType.INCOMPLETE_QUOTE

    def validate(
        self,
        outcome: OutcomeType,
        quote: QuoteDraft,
        transcript: list[TranscriptEvent],
        policy: CallPolicy,
    ) -> ValidatedOutcome:
        if not transcript:
            raise ValidationError("A call outcome requires transcript evidence")

        if outcome == OutcomeType.COMPLETE_QUOTE:
            return self._validate_complete(quote, policy)
        if outcome == OutcomeType.CALLBACK_REQUIRED:
            callback = self._terms(quote, "callback")
            if not callback:
                raise ValidationError(
                    "callback_required needs an evidence-backed callback commitment"
                )
            return ValidatedOutcome(outcome, [], str(callback[-1].value))
        if outcome == OutcomeType.DECLINED:
            declines = self._terms(quote, "decline")
            if not declines:
                raise ValidationError("declined needs an evidence-backed vendor refusal")
            return ValidatedOutcome(outcome, [], str(declines[-1].value))
        if outcome == OutcomeType.INCOMPLETE_QUOTE:
            warnings = self._missing_quote_warnings(quote, policy)
            return ValidatedOutcome(
                outcome,
                warnings,
                "The conversation ended without all fields required for a complete quote",
            )
        raise ValidationError(f"Unsupported outcome: {outcome}")

    def _validate_complete(
        self, quote: QuoteDraft, policy: CallPolicy
    ) -> ValidatedOutcome:
        totals = self._terms(quote, "estimated_total")
        if not totals:
            raise ValidationError("Complete quote requires an estimated total")
        total = totals[-1].value
        if not isinstance(total, (int, float)) or isinstance(total, bool):
            raise ValidationError("Estimated total must be numeric")
        if total < 0:
            raise ValidationError("Estimated total cannot be negative")
        if policy.require_itemization and not quote.line_items:
            raise ValidationError("Call policy requires at least one itemized cost")

        warnings: list[str] = []
        binding = self._terms(quote, "binding_status")
        if not binding or str(binding[-1].value).lower() == "unclear":
            warnings.append("Binding status was not established")
        if not self._terms(quote, "availability"):
            warnings.append("Vendor availability was not established")
        return ValidatedOutcome(OutcomeType.COMPLETE_QUOTE, warnings)

    def _missing_quote_warnings(
        self, quote: QuoteDraft, policy: CallPolicy
    ) -> list[str]:
        warnings: list[str] = []
        if not self._terms(quote, "estimated_total"):
            warnings.append("Estimated total is missing")
        if policy.require_itemization and not quote.line_items:
            refused = any(
                term.category == "itemization_status" and term.value == "refused"
                for term in quote.terms
            )
            warnings.append(
                "Vendor declined to itemize the quote"
                if refused
                else "Itemized costs are missing"
            )
        if not self._terms(quote, "binding_status"):
            warnings.append("Binding status is missing")
        return warnings

    @staticmethod
    def _terms(quote: QuoteDraft, category: str):
        return [term for term in quote.terms if term.category == category]
