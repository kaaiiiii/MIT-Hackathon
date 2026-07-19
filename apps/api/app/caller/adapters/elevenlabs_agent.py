from __future__ import annotations

import httpx

from ..errors import ExternalServiceError


class ElevenLabsCallerAgentAdapter:
    """Creates authenticated browser connections to one ElevenLabs Caller Agent."""

    def __init__(
        self,
        *,
        api_key: str,
        agent_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("ElevenLabs API key is required")
        if not agent_id.strip():
            raise ValueError("ElevenLabs Caller Agent ID is required")
        self.api_key = api_key
        self.agent_id = agent_id
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0)
        )

    async def create_connection(self) -> str:
        try:
            response = await self.client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": self.agent_id, "include_conversation_id": "true"},
                headers={"xi-api-key": self.api_key},
            )
            response.raise_for_status()
            signed_url = response.json().get("signed_url")
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError(
                "ElevenLabs Agents could not create the Caller connection "
                f"(HTTP {exc.response.status_code}: {self._safe_error_detail(exc.response)})"
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "ElevenLabs Agents could not reach the signed-URL service "
                f"({exc.__class__.__name__}: {exc})"
            ) from exc
        except ValueError as exc:
            raise ExternalServiceError(
                "ElevenLabs Agents returned an invalid Caller connection response"
            ) from exc
        if not isinstance(signed_url, str) or not signed_url.startswith("wss://"):
            raise ExternalServiceError(
                "ElevenLabs Agents returned an invalid Caller connection URL"
            )
        return signed_url

    async def fetch_conversation(self, conversation_id: str) -> dict:
        """Retrieve one completed conversation without exposing the API key."""
        try:
            response = await self.client.get(
                f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                headers={"xi-api-key": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise ExternalServiceError(
                "ElevenLabs could not retrieve the Caller conversation "
                f"(HTTP {exc.response.status_code}: {self._safe_error_detail(exc.response)})"
            ) from exc
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "ElevenLabs could not reach the conversation service "
                f"({exc.__class__.__name__}: {exc})"
            ) from exc
        except ValueError as exc:
            raise ExternalServiceError(
                "ElevenLabs returned an invalid Caller conversation"
            ) from exc
        if not isinstance(payload, dict):
            raise ExternalServiceError("ElevenLabs returned an invalid Caller conversation")
        if payload.get("agent_id") != self.agent_id:
            raise ExternalServiceError(
                "The ElevenLabs conversation does not belong to the configured Caller Agent"
            )
        return payload

    @staticmethod
    def _safe_error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "upstream request failed"
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            values = [
                detail.get("code"),
                detail.get("message"),
                f"request_id={detail['request_id']}" if detail.get("request_id") else None,
            ]
            return "; ".join(str(value) for value in values if value)
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        return "upstream request failed"

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
