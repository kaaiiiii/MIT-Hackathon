from __future__ import annotations

from typing import Protocol

from .schemas import CatalogCandidate, ParsedDocumentField


class IntakeVoiceConnection(Protocol):
    provider: str
    connection_url: str


class IntakeVoiceAdapter(Protocol):
    async def create_connection(
        self, *, session_id: str, context: dict
    ) -> tuple[str, str]: ...


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
