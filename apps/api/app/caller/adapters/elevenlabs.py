from __future__ import annotations

from ..errors import CallerError
from ..schemas import CallContext
from .base import BaseVoiceSessionAdapter


class ElevenLabsVoiceSessionAdapter(BaseVoiceSessionAdapter):
    """Provider seam. Wire an ElevenLabs SDK client here without domain leakage."""

    def __init__(self, client: object | None = None) -> None:
        self.client = client

    def _not_configured(self) -> CallerError:
        return CallerError(
            "ElevenLabs is not configured. Inject a provider client or use the "
            "simulated adapter."
        )

    async def create_session(self, call_context: CallContext) -> str:
        raise self._not_configured()

    async def send_context(self, session_id: str, context: CallContext) -> None:
        raise self._not_configured()

    async def end_session(self, session_id: str) -> None:
        raise self._not_configured()

    async def get_recording_reference(self, session_id: str) -> str | None:
        raise self._not_configured()

