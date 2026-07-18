from ..orchestrator import CallOrchestrator
from ..schemas import EvidenceInput, QuoteTerm, QuoteTermInput


class LogVendorRedFlagTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(
        self, call_id: str, key: str, detail: str, evidence: EvidenceInput
    ) -> QuoteTerm:
        return self.orchestrator.log_quote_term(
            call_id,
            QuoteTermInput(
                category="red_flag", key=key, value=detail, evidence=evidence
            ),
        )

