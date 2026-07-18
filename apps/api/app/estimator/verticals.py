from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .errors import EstimatorNotFoundError, EstimatorValidationError


class VerticalFieldConfig(BaseModel):
    type: str
    required: bool = False
    priority: int = 0
    question: str
    catalog_shaped: bool = False


class InterviewPlanConfig(BaseModel):
    max_attempts_per_field: int = Field(default=2, ge=1, le=5)
    completeness_threshold: float = Field(default=1.0, ge=0, le=1)


class DocumentTypeConfig(BaseModel):
    parser_hint: str
    review_threshold: float = Field(default=0.8, ge=0, le=1)


class VerticalConfig(BaseModel):
    vertical: str
    schema_version: str
    interview_plan: InterviewPlanConfig = Field(default_factory=InterviewPlanConfig)
    fields: dict[str, VerticalFieldConfig]
    document_types: dict[str, DocumentTypeConfig] = Field(default_factory=dict)
    catalog_resolvers: dict[str, list[str]] = Field(default_factory=dict)
    catalog_candidate_limit: int = Field(default=5, ge=1, le=20)
    benchmark_sources: list[str] = Field(default_factory=list)

    @property
    def required_fields(self) -> list[str]:
        return [name for name, field in self.fields.items() if field.required]


class VerticalConfigLoader:
    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory or Path(__file__).with_name("verticals"))

    @lru_cache(maxsize=32)
    def load(self, vertical: str) -> VerticalConfig:
        if not vertical.replace("_", "").isalnum():
            raise EstimatorValidationError("Invalid vertical name")
        path = self.directory / f"{vertical}.yaml"
        if not path.exists():
            raise EstimatorNotFoundError(f"Vertical {vertical!r} is not configured")
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = VerticalConfig.model_validate(payload)
        if config.vertical != vertical:
            raise EstimatorValidationError("Vertical filename and identifier differ")
        return config
