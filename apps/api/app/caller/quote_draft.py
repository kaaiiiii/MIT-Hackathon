from .evidence import EvidenceValidator
from .ports import CallerStore
from .schemas import QuoteLineItem, QuoteLineItemInput, QuoteTerm, QuoteTermInput


class QuoteDraftManager:
    def __init__(self, store: CallerStore):
        self.store = store
        self.evidence = EvidenceValidator(store)

    def log_line_item(
        self, call_id: str, item: QuoteLineItemInput
    ) -> QuoteLineItem:
        self.evidence.validate(call_id, item.evidence)
        return self.store.add_line_item(call_id, item)

    def log_term(self, call_id: str, term: QuoteTermInput) -> QuoteTerm:
        self.evidence.validate(call_id, term.evidence)
        return self.store.add_term(call_id, term)

