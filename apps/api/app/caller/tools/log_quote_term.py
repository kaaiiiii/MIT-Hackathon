from ..orchestrator import CallOrchestrator
from ..schemas import QuoteTerm, QuoteTermInput


class LogQuoteTermTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(self, call_id: str, term: QuoteTermInput) -> QuoteTerm:
        return self.orchestrator.log_quote_term(call_id, term)

