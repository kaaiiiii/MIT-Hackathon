import json

import httpx
import pytest

from apps.api.app.caller.adapters.elevenlabs_audio import ElevenLabsAudioAdapter


@pytest.mark.asyncio
async def test_elevenlabs_audio_adapter_uses_scribe_and_tts_contracts():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["xi-api-key"] == "test-key"
        if request.url.path.endswith("/speech-to-text"):
            body = await request.aread()
            assert b'scribe_v2' in body
            assert b'vendor.webm' in body
            return httpx.Response(200, json={"text": "The total is $475."})
        assert request.url.path.endswith("/text-to-speech/voice-123")
        assert request.url.params["output_format"] == "mp3_44100_128"
        payload = json.loads((await request.aread()).decode())
        assert payload == {
            "text": "What does that include?",
            "model_id": "eleven_flash_v2_5",
        }
        return httpx.Response(200, content=b"fake-mp3")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"xi-api-key": "test-key"},
    )
    adapter = ElevenLabsAudioAdapter(
        api_key="test-key",
        voice_id="voice-123",
        client=client,
    )

    transcript = await adapter.transcribe(
        b"fake-webm",
        filename="vendor.webm",
        media_type="audio/webm",
    )
    speech = await adapter.synthesize("What does that include?")

    assert transcript == "The total is $475."
    assert speech.content == b"fake-mp3"
    assert speech.media_type == "audio/mpeg"
    assert len(requests) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_elevenlabs_audio_adapter_streams_speech_chunks():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/text-to-speech/voice-123/stream")
        assert request.url.params["output_format"] == "mp3_44100_128"
        assert request.url.params["optimize_streaming_latency"] == "3"
        payload = json.loads((await request.aread()).decode())
        assert payload == {
            "text": "What does that include?",
            "model_id": "eleven_flash_v2_5",
        }
        return httpx.Response(200, content=b"chunked-fake-mp3")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"xi-api-key": "test-key"},
    )
    adapter = ElevenLabsAudioAdapter(
        api_key="test-key",
        voice_id="voice-123",
        client=client,
    )

    chunks = [
        chunk
        async for chunk in adapter.synthesize_stream("What does that include?")
    ]

    assert b"".join(chunks) == b"chunked-fake-mp3"
    await client.aclose()
