from ..orchestrator import CallOrchestrator
from ..schemas import EvidenceInput, QuoteTerm, QuoteTermInput


class RecordCallbackCommitmentTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(
        self, call_id: str, commitment: str, evidence: EvidenceInput
    ) -> QuoteTerm:
        return self.orchestrator.log_quote_term(
            call_id,
            QuoteTermInput(
                category="callback",
                key="vendor_callback_commitment",
                value=commitment,
                evidence=evidence,
            ),
        )

