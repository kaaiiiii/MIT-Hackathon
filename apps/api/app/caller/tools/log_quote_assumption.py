from ..orchestrator import CallOrchestrator
from ..schemas import EvidenceInput, QuoteTerm, QuoteTermInput


class LogQuoteAssumptionTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(
        self, call_id: str, key: str, value: object, evidence: EvidenceInput
    ) -> QuoteTerm:
        return self.orchestrator.log_quote_term(
            call_id,
            QuoteTermInput(
                category="assumption", key=key, value=value, evidence=evidence
            ),
        )

