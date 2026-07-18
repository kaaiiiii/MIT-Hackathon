from .errors import ValidationError
from .ports import CallerStore
from .schemas import EvidenceInput


class EvidenceValidator:
    def __init__(self, store: CallerStore):
        self.store = store

    def validate(self, call_id: str, evidence: EvidenceInput) -> None:
        event = self.store.get_transcript_event(evidence.transcript_event_id)
        if event is None:
            raise ValidationError(
                f"Transcript evidence {evidence.transcript_event_id!r} does not exist"
            )
        if event.call_id != call_id:
            raise ValidationError("Transcript evidence belongs to another call")
        if event.speaker != "vendor":
            raise ValidationError("Quote claims require a vendor transcript statement")
        if abs(event.timestamp_seconds - evidence.timestamp_seconds) > 2.0:
            raise ValidationError(
                "Evidence timestamp must match its transcript event within two seconds"
            )

