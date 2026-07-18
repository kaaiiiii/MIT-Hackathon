from __future__ import annotations

from pydantic import BaseModel

from .schemas import EvidencedField
from .verticals import VerticalConfig


class IntakeQuestionPlan(BaseModel):
    field_name: str
    objective: str
    spoken_question: str
    attempt: int
    allow_unknown: bool


class IntakeQuestionPlanner:
    """Chooses the highest-priority missing required field; it does not phrase freely."""

    def plan(
        self,
        config: VerticalConfig,
        fields: dict[str, EvidencedField],
        attempts: dict[str, int],
    ) -> IntakeQuestionPlan | None:
        missing = [
            (name, field)
            for name, field in config.fields.items()
            if field.required
            and (
                name not in fields
                or fields[name].value == "unknown"
                and not fields[name].unknown_acknowledged
            )
        ]
        if not missing:
            return None
        name, field = max(missing, key=lambda item: item[1].priority)
        attempt = attempts.get(name, 0) + 1
        allow_unknown = attempt >= config.interview_plan.max_attempts_per_field
        question = field.question
        if allow_unknown:
            question = (
                f"{question} If you don’t know, I can record it as unknown after you confirm."
            )
        return IntakeQuestionPlan(
            field_name=name,
            objective=f"elicit_{name}",
            spoken_question=question,
            attempt=attempt,
            allow_unknown=allow_unknown,
        )
