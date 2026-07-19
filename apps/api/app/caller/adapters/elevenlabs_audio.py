from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from ..audio import SynthesizedAudio
from ..errors import ExternalServiceError


class ElevenLabsAudioAdapter:
    """ElevenLabs Scribe STT and Text-to-Speech over the HTTP API."""

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str,
        stt_model: str = "scribe_v2",
        tts_model: str = "eleven_flash_v2_5",
        output_format: str = "mp3_44100_128",
        stream_latency: int = 3,
        base_url: str = "https://api.elevenlabs.io/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("ElevenLabs API key is required")
        if not voice_id.strip():
            raise ValueError("ElevenLabs voice ID is required")
        self.voice_id = voice_id
        self.stt_model = stt_model
        self.tts_model = tts_model
        self.output_format = output_format
        self.stream_latency = stream_latency
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"xi-api-key": api_key},
            timeout=httpx.Timeout(60.0, connect=15.0),
        )

    async def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        media_type: str,
    ) -> str:
        if not audio:
            raise ExternalServiceError("The recorded audio was empty.")
        try:
            response = await self.client.post(
                f"{self.base_url}/speech-to-text",
                data={"model_id": self.stt_model},
                files={"file": (filename, audio, media_type)},
            )
            response.raise_for_status()
            payload: Any = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalServiceError(
                "ElevenLabs could not transcribe the recording."
            ) from exc

        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ExternalServiceError(
                "ElevenLabs returned no spoken text for this recording."
            )
        return text.strip()

    async def synthesize(self, text: str) -> SynthesizedAudio:
        if not text.strip():
            raise ExternalServiceError("Cannot synthesize an empty buyer response.")
        safe_voice_id = quote(self.voice_id, safe="")
        try:
            response = await self.client.post(
                f"{self.base_url}/text-to-speech/{safe_voice_id}",
                params={"output_format": self.output_format},
                json={"text": text, "model_id": self.tts_model},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                "ElevenLabs could not generate the buyer voice."
            ) from exc
        if not response.content:
            raise ExternalServiceError("ElevenLabs returned empty speech audio.")
        return SynthesizedAudio(content=response.content, media_type="audio/mpeg")

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield speech chunks as ElevenLabs renders them, for progressive playback."""
        if not text.strip():
            raise ExternalServiceError("Cannot synthesize an empty buyer response.")
        safe_voice_id = quote(self.voice_id, safe="")
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/text-to-speech/{safe_voice_id}/stream",
                params={
                    "output_format": self.output_format,
                    "optimize_streaming_latency": self.stream_latency,
                },
                json={"text": text, "model_id": self.tts_model},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:
            raise ExternalServiceError(
                "ElevenLabs could not stream the buyer voice."
            ) from exc

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
