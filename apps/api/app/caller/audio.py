from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    content: bytes
    media_type: str


class AudioPipelineAdapter(Protocol):
    """Provider-neutral speech-to-text and text-to-speech boundary."""

    async def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        media_type: str,
    ) -> str: ...

    async def synthesize(self, text: str) -> SynthesizedAudio: ...

    async def close(self) -> None: ...
