from ..orchestrator import CallOrchestrator
from ..schemas import EvidenceInput, QuoteTerm, QuoteTermInput


class RecordVendorDeclineTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(
        self, call_id: str, reason: str, evidence: EvidenceInput
    ) -> QuoteTerm:
        return self.orchestrator.log_quote_term(
            call_id,
            QuoteTermInput(
                category="decline",
                key="vendor_decline_reason",
                value=reason,
                evidence=evidence,
            ),
        )

