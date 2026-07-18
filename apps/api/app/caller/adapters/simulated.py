from __future__ import annotations

from uuid import uuid4

from ..schemas import CallContext
from .base import BaseVoiceSessionAdapter


class SimulatedVoiceSessionAdapter(BaseVoiceSessionAdapter):
    """Deterministic adapter for API development and state-machine tests."""

    def __init__(self) -> None:
        self.sessions: dict[str, CallContext] = {}
        self.ended_sessions: set[str] = set()

    async def create_session(self, call_context: CallContext) -> str:
        session_id = f"sim_{uuid4().hex}"
        self.sessions[session_id] = call_context
        return session_id

    async def send_context(self, session_id: str, context: CallContext) -> None:
        if session_id not in self.sessions:
            raise KeyError(f"Unknown simulated session: {session_id}")
        self.sessions[session_id] = context

    async def end_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            self.ended_sessions.add(session_id)

    async def get_recording_reference(self, session_id: str) -> str | None:
        if session_id not in self.sessions:
            return None
        return f"simulated://recordings/{session_id}"

