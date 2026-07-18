from ..orchestrator import CallOrchestrator
from ..schemas import QuoteLineItem, QuoteLineItemInput


class LogQuoteLineItemTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(self, call_id: str, item: QuoteLineItemInput) -> QuoteLineItem:
        return self.orchestrator.log_quote_line_item(call_id, item)

