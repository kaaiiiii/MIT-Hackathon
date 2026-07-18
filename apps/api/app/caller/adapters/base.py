from abc import ABC, abstractmethod

from ..schemas import CallContext


class BaseVoiceSessionAdapter(ABC):
    @abstractmethod
    async def create_session(self, call_context: CallContext) -> str: ...

    @abstractmethod
    async def send_context(self, session_id: str, context: CallContext) -> None: ...

    @abstractmethod
    async def end_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def get_recording_reference(self, session_id: str) -> str | None: ...

