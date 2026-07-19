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
        if not api_key.strip():
            raise ValueError("ElevenLabs API key is required")
        if not agent_id.strip():
            raise ValueError("ElevenLabs intake Agent ID is required")
        self.api_key = api_key
        self.agent_id = agent_id
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
        )

    async def create_connection(
        self, *, session_id: str, context: dict
    ) -> tuple[str, str]:
        try:
            response = await self.client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": self.agent_id},
                headers={"xi-api-key": self.api_key},
            )
            response.raise_for_status()
            signed_url = response.json().get("signed_url")
        except httpx.HTTPStatusError as exc:
            detail = self._safe_error_detail(exc.response)
            raise EstimatorValidationError(
                "ElevenLabs Agents could not create the intake voice connection "
                f"(HTTP {exc.response.status_code}: {detail})"
            ) from exc
        except httpx.RequestError as exc:
            raise EstimatorValidationError(
                "ElevenLabs Agents could not reach the signed-URL service "
                f"({exc.__class__.__name__}: {exc})"
            ) from exc
        except ValueError as exc:
            raise EstimatorValidationError(
                "ElevenLabs Agents returned an invalid signed-URL response"
            ) from exc
        if not isinstance(signed_url, str) or not signed_url.startswith("wss://"):
            raise EstimatorValidationError(
                "ElevenLabs Agents returned an invalid connection URL"
            )
        return "elevenlabs_agents", signed_url

    @staticmethod
    def _safe_error_detail(response: httpx.Response) -> str:
        """Return useful ElevenLabs diagnostics without echoing credentials."""
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
