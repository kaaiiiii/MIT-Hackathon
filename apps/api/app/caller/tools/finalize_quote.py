from ..orchestrator import CallOrchestrator
from ..schemas import OutcomeType, StructuredCallOutcome


class FinalizeQuoteTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    async def __call__(
        self, call_id: str, requested_outcome: OutcomeType | None = None
    ) -> StructuredCallOutcome:
        return await self.orchestrator.finalize_call(call_id, requested_outcome)

