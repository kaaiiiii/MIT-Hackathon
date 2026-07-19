from __future__ import annotations

from typing import Protocol

from .schemas import (
    CatalogCandidate,
    ElevenLabsConversationTranscript,
    ParsedDocumentField,
    TranscriptFieldExtraction,
)
from .verticals import VerticalConfig


class IntakeVoiceConnection(Protocol):
    provider: str
    connection_url: str


class IntakeVoiceAdapter(Protocol):
    async def create_connection(
        self, *, session_id: str, context: dict
    ) -> tuple[str, str]: ...


class ConversationTranscriptImporter(Protocol):
    async def fetch_conversation(
        self, *, conversation_id: str, expected_session_id: str
    ) -> ElevenLabsConversationTranscript: ...


class TranscriptFieldExtractor(Protocol):
    model_name: str

    async def extract_fields(
        self,
        *,
        config: VerticalConfig,
        transcript: ElevenLabsConversationTranscript,
    ) -> TranscriptFieldExtraction: ...


class DocumentParser(Protocol):
    async def parse(
        self,
        *,
        document_id: str,
        document_type: str,
        content: bytes,
        media_type: str,
    ) -> list[ParsedDocumentField]: ...


class CatalogResolver(Protocol):
    async def search(
        self, *, field_name: str, raw_statement: str, catalog_name: str
    ) -> list[CatalogCandidate]: ...
