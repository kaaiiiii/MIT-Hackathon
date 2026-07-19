from __future__ import annotations

import json

import httpx

from .errors import EstimatorValidationError
from .schemas import (
    CatalogCandidate,
    ElevenLabsConversationTranscript,
    ElevenLabsTranscriptTurn,
    ParsedDocumentField,
)


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

    async def fetch_conversation(
        self, *, conversation_id: str, expected_session_id: str
    ) -> ElevenLabsConversationTranscript:
        try:
            response = await self.client.get(
                f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
                headers={"xi-api-key": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            detail = self._safe_error_detail(exc.response)
            raise EstimatorValidationError(
                "ElevenLabs could not retrieve the completed intake conversation "
                f"(HTTP {exc.response.status_code}: {detail})"
            ) from exc
        except httpx.RequestError as exc:
            raise EstimatorValidationError(
                "ElevenLabs could not reach the conversation history service "
                f"({exc.__class__.__name__}: {exc})"
            ) from exc
        except ValueError as exc:
            raise EstimatorValidationError(
                "ElevenLabs returned an invalid conversation response"
            ) from exc

        if not isinstance(payload, dict):
            raise EstimatorValidationError("ElevenLabs returned an invalid conversation")
        if payload.get("agent_id") != self.agent_id:
            raise EstimatorValidationError(
                "The ElevenLabs conversation belongs to a different intake Agent"
            )
        if payload.get("status") != "done":
            raise EstimatorValidationError(
                "The ElevenLabs conversation is not ready; wait for post-call processing"
            )
        initiation = payload.get("conversation_initiation_client_data")
        session_ids = self._find_values(initiation, "intake_session_id")
        if expected_session_id not in session_ids:
            raise EstimatorValidationError(
                "The ElevenLabs conversation does not belong to this Estimator session"
            )

        turns: list[ElevenLabsTranscriptTurn] = []
        for index, item in enumerate(payload.get("transcript") or []):
            if not isinstance(item, dict) or item.get("role") not in {"user", "agent"}:
                continue
            message = item.get("message")
            if not isinstance(message, str) or not message.strip():
                continue
            raw_time = item.get("time_in_call_secs")
            time_in_call_secs = float(raw_time) if isinstance(raw_time, (int, float)) else 0
            turns.append(
                ElevenLabsTranscriptTurn(
                    turn_id=f"el_{conversation_id}_{index}",
                    role=item["role"],
                    message=message.strip(),
                    time_in_call_secs=max(time_in_call_secs, 0),
                )
            )
        if not any(turn.role == "user" for turn in turns):
            raise EstimatorValidationError(
                "The ElevenLabs conversation contains no user transcript turns"
            )
        metadata = payload.get("metadata")
        start_time = metadata.get("start_time_unix_secs") if isinstance(metadata, dict) else None
        return ElevenLabsConversationTranscript(
            conversation_id=conversation_id,
            agent_id=self.agent_id,
            status="done",
            intake_session_id=expected_session_id,
            start_time_unix_secs=start_time if isinstance(start_time, int) else None,
            turns=turns,
        )

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

    @staticmethod
    def _find_values(value: object, key: str) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                if item_key == key and isinstance(item_value, str):
                    found.add(item_value)
                found.update(ElevenLabsAgentsIntakeAdapter._find_values(item_value, key))
        elif isinstance(value, list):
            for item in value:
                found.update(ElevenLabsAgentsIntakeAdapter._find_values(item, key))
        return found

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
