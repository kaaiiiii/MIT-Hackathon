from __future__ import annotations

import json

import httpx

from .errors import EstimatorValidationError
from .schemas import CatalogCandidate, ParsedDocumentField


class StructuredJsonDocumentParser:
    """Deterministic document seam for golden tests and structured manifests."""

    async def parse(
        self,
        *,
        document_id: str,
        document_type: str,
        content: bytes,
        media_type: str,
    ) -> list[ParsedDocumentField]:
        if media_type not in {"application/json", "text/json", "application/octet-stream"}:
            raise EstimatorValidationError(
                "The built-in parser accepts structured JSON documents; inject a "
                "vision/OCR parser for photos or PDFs"
            )
        try:
            payload = json.loads(content.decode("utf-8"))
            fields = payload["fields"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise EstimatorValidationError("Invalid structured document payload") from exc
        if not isinstance(fields, list):
            raise EstimatorValidationError("Document fields must be a list")
        return [ParsedDocumentField.model_validate(item) for item in fields]


class InMemoryCatalogResolver:
    def __init__(
        self,
        entries: dict[tuple[str, str], list[CatalogCandidate]] | None = None,
    ) -> None:
        self.entries = entries or {}

    async def search(
        self, *, field_name: str, raw_statement: str, catalog_name: str
    ) -> list[CatalogCandidate]:
        exact = self.entries.get((catalog_name, raw_statement.casefold()))
        if exact is not None:
            return list(exact)
        return []


class SimulatedIntakeVoiceAdapter:
    async def create_connection(
        self, *, session_id: str, context: dict
    ) -> tuple[str, str]:
        return "simulated", f"simulated://elevenlabs-agent/{session_id}"


class ElevenLabsAgentsIntakeAdapter:
    """Creates an authenticated ElevenLabs Agents WebSocket connection URL."""

    def __init__(
        self,
        *,
        api_key: str,
        agent_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"xi-api-key": api_key},
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    async def create_connection(
        self, *, session_id: str, context: dict
    ) -> tuple[str, str]:
        try:
            response = await self.client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": self.agent_id},
            )
            response.raise_for_status()
            signed_url = response.json().get("signed_url")
        except (httpx.HTTPError, ValueError) as exc:
            raise EstimatorValidationError(
                "ElevenLabs Agents could not create the intake voice connection"
            ) from exc
        if not isinstance(signed_url, str) or not signed_url.startswith("wss://"):
            raise EstimatorValidationError(
                "ElevenLabs Agents returned an invalid connection URL"
            )
        return "elevenlabs_agents", signed_url

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
