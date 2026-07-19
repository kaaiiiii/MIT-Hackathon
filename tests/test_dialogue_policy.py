import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.dialogue_planner import DialogueAction
from apps.api.app.caller.openai_agent import (
    BuyerTurnDecision,
    ExtractedLineItem,
    ExtractedTerm,
    OpenAIBuyerTurnModel,
    SpokenTurn,
)
from apps.api.app.caller.schemas import CallView
from apps.api.app.caller.spoken_response import validate_spoken_response


def _send(client: TestClient, call_id: str, text: str) -> dict:
    response = client.post(
        f"/api/v1/demo/sessions/{call_id}/messages", json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


class _AdaptiveResponses:
    """A deterministic stand-in for GPT's typed adaptive decision."""

    def __init__(self, *, unusual_itemization_wording: bool = False):
        self.payloads: list[dict] = []
        self.unusual_itemization_wording = unusual_itemization_wording

    async def parse(self, **kwargs):
        payload = json.loads(kwargs["input"])
        if kwargs["text_format"] is SpokenTurn:
            return SimpleNamespace(
                output_parsed=SpokenTurn(
                    spoken_response=(
                        "Hi, I'm an AI assistant calling for a buyer. "
                        "Is now a good time to discuss a moving quote?"
                    )
                )
            )

        assert kwargs["text_format"] is BuyerTurnDecision
        self.payloads.append(payload)
        latest = payload["latest_vendor_statement"].casefold()
        allowed = payload.get("allowed_next_actions_after_analysis") or payload[
            "candidate_next_actions"
        ]
        terms: list[ExtractedTerm] = []
        line_items: list[ExtractedLineItem] = []
        relation = "answered"
        intent = "continue"

        if "what are the details" in latest or "can discuss" in latest:
            action = DialogueAction.PRESENT_JOB
            spoken = "It's one upright piano from Brooklyn to Queens. What would you charge for the move?"
        elif "depends on the piano" in latest:
            action = DialogueAction.REQUEST_PROVISIONAL_RANGE
            relation = "partially_answered"
            terms = [
                ExtractedTerm(
                    category="vendor_requirement",
                    key="dimensions_and_weight",
                    value_text="Dimensions and weight required",
                )
            ]
            spoken = "We don't have those measurements yet. Could you give a provisional range for a standard upright?"
        elif "really need the dimensions" in latest:
            action = DialogueAction.REQUEST_CALLBACK_REQUIREMENTS
            relation = "partially_answered"
            terms = [
                ExtractedTerm(
                    category="vendor_requirement",
                    key="quote_inputs",
                    value_text="Dimensions, weight, and exact addresses required",
                )
            ]
            spoken = "What exact information should I send, and how soon could you quote after receiving it?"
        elif "respond within a day" in latest:
            action = DialogueAction.CLOSE_CALLBACK_REQUIRED
            intent = "callback"
            spoken = "Perfect. I'll record this as a callback quote once we send the measurements, addresses, and photos."
        elif latest.strip(" .") in {"flat", "it is a flat price"}:
            action = DialogueAction.REQUEST_ITEMIZATION
            terms = [
                ExtractedTerm(
                    category="pricing_model",
                    key="pricing_model",
                    value_text="flat",
                )
            ]
            spoken = (
                "Please provide component amounts?"
                if self.unusual_itemization_wording
                else "What charges make up that flat price?"
            )
        elif "labor is $350" in latest:
            action = DialogueAction.REQUEST_TOTAL
            line_items = [
                ExtractedLineItem(
                    category="labor", description="Moving labor", amount=350
                ),
                ExtractedLineItem(
                    category="stairs", description="Stair charge", amount=75
                ),
            ]
            spoken = "What total should the customer expect?"
        elif "don't want to disclose" in latest or "don’t want to disclose" in latest:
            action = DialogueAction.REQUEST_TOTAL
            relation = "refused"
            terms = [
                ExtractedTerm(
                    category="itemization_status",
                    key="vendor_itemization_refusal",
                    value_text="refused",
                )
            ]
            spoken = "No problem—what total should the customer expect?"
        elif "flat total is $400" in latest:
            action = DialogueAction.CLARIFY_FEES
            terms = [
                ExtractedTerm(
                    category="pricing_model",
                    key="pricing_model",
                    value_text="flat",
                ),
                ExtractedTerm(
                    category="estimated_total",
                    key="total",
                    value_number=400,
                ),
            ]
            line_items = [
                ExtractedLineItem(
                    category="labor", description="Moving labor", amount=400
                )
            ]
            spoken = "Does that $400 include every fee?"
        elif latest.strip(" .") in {"yes", "correct", "that's correct"}:
            action = DialogueAction.CLARIFY_BINDING
            terms = [
                ExtractedTerm(
                    category="fee",
                    key="additional_fees",
                    value_text="No additional fees confirmed",
                    evidence_source="vendor_confirmation",
                )
            ]
            spoken = "Will you hold that $400 price for the customer?"
        elif "$300 to $500" in latest:
            action = DialogueAction.REQUEST_ITEMIZATION
            terms = [
                ExtractedTerm(
                    category="estimated_total",
                    key="quoted_range",
                    value_text="$300 to $500 range",
                )
            ]
            spoken = "What parts of that range are priced separately?"
        elif "flowers" in latest:
            action = DialogueAction.CLARIFY_FEES
            relation = "off_topic"
            spoken = "Flowers are nice. Coming back to the move, does that range include any extra fees?"
        else:
            action = DialogueAction(allowed[0])
            spoken = "What would be the most useful next detail to settle the quote?"

        assert action.value in allowed
        return SimpleNamespace(
            output_parsed=BuyerTurnDecision(
                understanding=f"Vendor response classified as {relation}.",
                response_relation=relation,
                intent=intent,
                line_items=line_items,
                terms=terms,
                planned_action=action,
                spoken_response=spoken,
            )
        )


def _app_with_adaptive_model(tmp_path, responses, name="adaptive.db"):
    model = OpenAIBuyerTurnModel(
        api_key="unused-test-key",
        client=SimpleNamespace(responses=responses),
    )
    return create_app(database_path=str(tmp_path / name), buyer_model=model)


def test_missing_information_blocker_resolves_within_two_buyer_turns(tmp_path):
    app = _app_with_adaptive_model(tmp_path, _AdaptiveResponses(), "blocker.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Sure. What are the details?")

        body = _send(
            client,
            call_id,
            "That depends on the piano size and weight. I need those to quote.",
        )
        assert "provisional" in body["agent_message"].casefold()

        body = _send(
            client,
            call_id,
            "I really need the dimensions, weight, and exact addresses.",
        )
        assert "what exact information" in body["agent_message"].casefold()

        body = _send(
            client,
            call_id,
            "Dimensions, weight, both addresses, and photos. We can respond within a day.",
        )
        assert body["terminal"] is True
        assert body["call"]["outcome"]["outcome_type"] == "callback_required"


def test_spoken_response_validator_rejects_robotic_or_repeated_turns():
    assert not validate_spoken_response(
        "Understood. Do you need dimensions? What is the total?"
    ).valid
    assert not validate_spoken_response(
        "Can you walk me through your pricing model?"
    ).valid
    repeated = validate_spoken_response(
        "What rough price range could you give for a standard upright?",
        recent_buyer_turns=[
            "Could you give me a rough price range for a standard upright?"
        ],
    )
    assert not repeated.valid
    repaired = validate_spoken_response(
        "Coming back to the move, could you give a rough range for the upright?",
        recent_buyer_turns=[
            "Could you give me a rough price range for a standard upright?"
        ],
        allow_question_repair=True,
    )
    assert repaired.valid


def test_runtime_loads_policy_before_adaptive_agent():
    model = OpenAIBuyerTurnModel(
        api_key="unused-test-key",
        client=SimpleNamespace(responses=SimpleNamespace()),
    )
    assert "COMMUNICATION AND NEGOTIATION POLICY" in model.communication_policy
    assert "Principled negotiation" in model.communication_policy
    assert "Default to one open question" in model.communication_policy
    assert "ADAPTIVE BUYER TURN" in model.adaptive_instructions
    assert model.adaptive_instructions.index(
        "COMMUNICATION AND NEGOTIATION POLICY"
    ) < model.adaptive_instructions.index("ADAPTIVE BUYER TURN")


class _RetryResponses:
    def __init__(self):
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        invalid = len(self.calls) == 1
        return SimpleNamespace(
            output_parsed=BuyerTurnDecision(
                understanding="The vendor requires missing dimensions and weight.",
                response_relation="partially_answered",
                terms=[
                    ExtractedTerm(
                        category="vendor_requirement",
                        key="dimensions_and_weight",
                        value_text="Dimensions and weight required",
                    )
                ],
                planned_action=DialogueAction.REQUEST_PROVISIONAL_RANGE,
                spoken_response=(
                    "Understood. Do you need dimensions? What is your pricing model?"
                    if invalid
                    else "We don't have those measurements yet. Could you give a provisional range for a standard upright?"
                ),
            )
        )


@pytest.mark.asyncio
async def test_openai_decision_retries_once_when_spoken_response_is_invalid(tmp_path):
    setup_responses = _AdaptiveResponses()
    app = _app_with_adaptive_model(tmp_path, setup_responses, "retry-view.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        body = _send(client, call_id, "Sure. What are the details?")
        view = CallView.model_validate(body["call"])

    responses = _RetryResponses()
    model = OpenAIBuyerTurnModel(
        api_key="unused-test-key",
        client=SimpleNamespace(responses=responses),
    )
    decision = await model.respond(
        view, "I need the dimensions and weight before I can quote."
    )

    assert len(responses.calls) == 2
    assert decision.planned_action == DialogueAction.REQUEST_PROVISIONAL_RANGE
    assert decision.terms[0].category == "vendor_requirement"
    assert "decision_validation_failure" in responses.calls[1]["input"]


def test_gpt_decision_extracts_terse_flat_answer_then_asks_for_components(tmp_path):
    responses = _AdaptiveResponses()
    app = _app_with_adaptive_model(tmp_path, responses, "flat.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Yes, I can discuss it.")
        body = _send(client, call_id, "flat")

    assert responses.payloads[-1]["candidate_next_actions"][:4] == [
        "request_pricing_model",
        "request_itemization",
        "request_total",
        "clarify_fees",
    ]
    assert body["agent_message"] == "What charges make up that flat price?"
    pricing = [
        term
        for term in body["call"]["original_quote"]["terms"]
        if term["category"] == "pricing_model"
    ]
    assert [term["value"] for term in pricing] == ["flat"]


def test_itemized_answer_is_extracted_without_another_breakdown_question(tmp_path):
    responses = _AdaptiveResponses()
    app = _app_with_adaptive_model(tmp_path, responses, "itemized.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Yes, I can discuss it.")
        _send(client, call_id, "It is a flat price.")
        body = _send(client, call_id, "Labor is $350 and the stair charge is $75.")

    assert body["agent_message"] == "What total should the customer expect?"
    assert [
        item["amount"] for item in body["call"]["original_quote"]["line_items"]
    ] == [350, 75]


@pytest.mark.parametrize("apostrophe", ["don't", "don’t"])
def test_semantic_refusal_is_respected_without_reasking_itemization(
    tmp_path, apostrophe
):
    responses = _AdaptiveResponses(unusual_itemization_wording=True)
    app = _app_with_adaptive_model(tmp_path, responses, f"refusal-{ord(apostrophe[3])}.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Yes, I can discuss it.")
        first = _send(client, call_id, "It is a flat price.")
        assert first["agent_message"] == "Please provide component amounts?"
        body = _send(client, call_id, f"I {apostrophe} want to disclose.")

    assert body["agent_message"] == "No problem—what total should the customer expect?"
    assert body["call"]["dialogue_actions"].count("request_itemization") == 1
    refusal = [
        term
        for term in body["call"]["original_quote"]["terms"]
        if term["category"] == "itemization_status"
    ]
    assert [term["value"] for term in refusal] == ["refused"]


def test_terse_yes_is_understood_in_context_then_agent_advances(tmp_path):
    responses = _AdaptiveResponses()
    app = _app_with_adaptive_model(tmp_path, responses, "context.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Yes, I can discuss it.")
        fee_question = _send(
            client, call_id, "The flat total is $400 and labor is $400."
        )
        assert fee_question["agent_message"] == "Does that $400 include every fee?"
        body = _send(client, call_id, "Yes.")

    assert body["agent_message"] == "Will you hold that $400 price for the customer?"
    fee_terms = [
        term
        for term in body["call"]["original_quote"]["terms"]
        if term["category"] == "fee"
    ]
    assert fee_terms[0]["evidence"]["source"] == "vendor_confirmation"


def test_off_topic_vendor_reply_is_acknowledged_without_false_quote_progress(tmp_path):
    responses = _AdaptiveResponses()
    app = _app_with_adaptive_model(tmp_path, responses, "off-topic.db")
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        _send(client, call_id, "Yes, I can discuss it.")
        _send(client, call_id, "The range is $300 to $500.")
        body = _send(client, call_id, "I like flowers")

    reply = body["agent_message"]
    assert reply.startswith("Flowers are nice.")
    assert "range helps" not in reply.casefold()
    assert "extra fees" in reply.casefold()
    assert body["call"]["dialogue_actions"][-1] == "clarify_fees"
