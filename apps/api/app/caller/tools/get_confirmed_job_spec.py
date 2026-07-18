from ..orchestrator import CallOrchestrator
from ..schemas import ConfirmedJobSpec


class GetConfirmedJobSpecTool:
    def __init__(self, orchestrator: CallOrchestrator) -> None:
        self.orchestrator = orchestrator

    def __call__(self, call_id: str) -> ConfirmedJobSpec:
        return self.orchestrator.get_confirmed_job_spec(call_id)

