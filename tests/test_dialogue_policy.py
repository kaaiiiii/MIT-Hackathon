from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.openai_agent import (
    BuyerTurnDecision,
    ExtractedTerm,
    OpenAIBuyerTurnModel,
)
from apps.api.app.caller.schemas import CallView
from apps.api.app.caller.spoken_response import validate_spoken_response


def _send(client: TestClient, call_id: str, text: str) -> dict:
    response = client.post(
        f"/api/v1/demo/sessions/{call_id}/messages", json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_missing_information_blocker_resolves_within_two_buyer_turns(tmp_path):
    app = create_app(database_path=str(tmp_path / "blocker.db"))
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Sure. What are the details?")

        body = _send(
            client,
            call_id,
            "That depends on the piano size and weight. I need those to quote.",
        )
        first = body["agent_message"]
        assert "provisional" in first.casefold()
        assert first.count("?") == 1
        assert len(first.split()) < 30

        body = _send(
            client,
            call_id,
            "I really need the dimensions, weight, and exact addresses.",
        )
        second = body["agent_message"]
        assert "what exact information" in second.casefold()
        assert second.count("?") == 1
        assert "piano size isn’t specified" not in second.casefold()

        body = _send(
            client,
            call_id,
            "Dimensions, weight, both addresses, and photos. We can respond within a day.",
        )
        assert body["terminal"] is True
        assert body["call"]["outcome"]["outcome_type"] == "callback_required"
        assert "callback" in body["agent_message"].casefold()


def test_spoken_response_validator_rejects_robotic_or_multi_question_turns():
    assert not validate_spoken_response(
        "Understood. Do you need dimensions? What is the total?"
    ).valid
    assert not validate_spoken_response(
        "Can you walk me through your pricing model?"
    ).valid
    assert validate_spoken_response(
        "We don’t have the measurements yet. Could you quote a standard upright provisionally?"
    ).valid


def test_spoken_response_validator_rejects_a_rephrased_recent_question():
    check = validate_spoken_response(
        "What rough price range could you give for a standard upright?",
        recent_buyer_turns=[
            "Could you give me a rough price range for a standard upright?"
        ],
    )
    assert not check.valid
    assert "recent buyer question" in (check.reason or "")


def test_runtime_loads_broad_policy_before_surface_realizer():
    model = OpenAIBuyerTurnModel(
        api_key="unused-test-key",
        client=SimpleNamespace(responses=SimpleNamespace()),
    )
    assert "COMMUNICATION AND NEGOTIATION POLICY" in model.communication_policy
    assert "Principled negotiation" in model.communication_policy
    assert "Default to one open question" in model.communication_policy
    assert "SURFACE REALIZATION AND SAME-TURN EXTRACTION" in model.surface_instructions
    assert model.instructions.index("COMMUNICATION AND NEGOTIATION POLICY") < (
        model.instructions.index("SURFACE REALIZATION AND SAME-TURN EXTRACTION")
    )


class _FakeResponses:
    def __init__(self):
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            decision = BuyerTurnDecision(
                spoken_response="Understood. Do you need dimensions? What is your pricing model?",
                terms=[
                    ExtractedTerm(
                        category="vendor_requirement",
                        key="dimensions",
                        value_text="dimensions required",
                    )
                ],
            )
        else:
            decision = BuyerTurnDecision(
                spoken_response=(
                    "We don’t have those measurements yet. Could you give a "
                    "provisional range for a standard upright?"
                )
            )
        return SimpleNamespace(output_parsed=decision)


@pytest.mark.asyncio
async def test_openai_surface_realization_retries_once_without_changing_extraction(
    tmp_path,
):
    app = create_app(database_path=str(tmp_path / "retry.db"))
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        body = _send(client, call_id, "Sure. What are the details?")
        view = CallView.model_validate(body["call"])

    responses = _FakeResponses()
    model = OpenAIBuyerTurnModel(
        api_key="unused-test-key",
        job_facts={"service": "Move one upright piano"},
        client=SimpleNamespace(responses=responses),
    )
    decision = await model.respond(
        view,
        "I need the dimensions and weight before I can quote.",
    )

    assert len(responses.calls) == 2
    assert decision.planned_action == "request_provisional_range"
    assert decision.spoken_response.count("?") == 1
    assert decision.terms[0].category == "vendor_requirement"
    assert "response_validation_failure" in responses.calls[1]["input"]
