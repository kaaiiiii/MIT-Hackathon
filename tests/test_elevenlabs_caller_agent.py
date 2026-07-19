from fastapi.testclient import TestClient

from apps.api.app.caller.dialogue_planner import DialogueAction
from apps.api.app.caller.openai_agent import (
    BuyerTurnAdvice,
    ExtractedLineItem,
    ExtractedTerm,
)
from apps.api.app.main import create_app


class FakeCallerAgentAdapter:
    async def create_connection(self):
        return "wss://api.elevenlabs.test/caller?signed=true"

    async def close(self):
        pass


class FakeAdvisor:
    model_name = "gpt-adviser-test"

    async def opening(self, view, job_facts=None):
        return "Legacy demo opening"

    async def respond(self, view, vendor_text, job_facts=None):
        raise AssertionError("The ElevenLabs loop must not ask GPT to write speech")

    async def advise(
        self,
        view,
        vendor_text,
        job_facts=None,
        provider_conversation_history=None,
    ):
        lowered = vendor_text.casefold()
        if view.call.status.value == "disclosure":
            return BuyerTurnAdvice(
                planned_action=DialogueAction.PRESENT_JOB,
                conversational_objective="Present the confirmed job and invite a quote.",
            )
        if "labor is" in lowered:
            return BuyerTurnAdvice(
                planned_action=DialogueAction.CLARIFY_FEES,
                conversational_objective="Find out whether any charges remain outside the total.",
                line_items=[
                    ExtractedLineItem(
                        category="labor", description="Moving labor", amount=400
                    )
                ],
                terms=[
                    ExtractedTerm(
                        category="pricing_model",
                        key="pricing_model",
                        value_text="flat",
                    ),
                    ExtractedTerm(
                        category="estimated_total", key="total", value_number=400
                    ),
                ],
            )
        if "no extra fees" in lowered:
            return BuyerTurnAdvice(
                planned_action=DialogueAction.CONFIRM_SUMMARY,
                conversational_objective="Read back the stored quote for correction.",
                terms=[
                    ExtractedTerm(
                        category="fee",
                        key="additional_fees",
                        value_text="No extra fees",
                    ),
                    ExtractedTerm(
                        category="binding_status",
                        key="binding_status",
                        value_text="binding",
                    ),
                    ExtractedTerm(
                        category="availability",
                        key="availability",
                        value_text="Available August 1",
                    ),
                ],
            )
        return BuyerTurnAdvice(
            intent="confirm_summary",
            planned_action=DialogueAction.CONFIRM_SUMMARY,
            conversational_objective="Close politely with the confirmed quote.",
        )


def post(client, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def record(client, call_id, spoken_text, advice_id=None):
    return post(
        client,
        f"/api/v1/demo/agent/sessions/{call_id}/utterances",
        {"spoken_text": spoken_text, "advice_id": advice_id},
    )


def advise(client, call_id, vendor_text):
    return post(
        client,
        f"/api/v1/demo/agent/sessions/{call_id}/advise",
        {"vendor_text": vendor_text, "conversation_history": {"entries": []}},
    )


def test_elevenlabs_owns_speech_while_gpt_advises_and_backend_stores_evidence(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "caller-agent.db"),
        buyer_model=FakeAdvisor(),
        caller_agent_adapter=FakeCallerAgentAdapter(),
    )
    with TestClient(app) as client:
        started = post(client, "/api/v1/demo/agent/sessions", {})
        call_id = started["call"]["call"]["call_id"]
        assert started["provider_connection_url"].startswith("wss://")
        assert started["required_client_tools"] == [
            "advise_caller_turn",
            "record_caller_utterance",
        ]
        assert "confirmed_job_spec_json" in started["provider_context"]
        assert "agent_message" not in started

        decision = advise(client, call_id, "Yes, I can discuss it.")
        assert decision["advice"]["conversational_objective"].startswith("Present")
        assert "spoken_response" not in decision["advice"]
        record(
            client,
            call_id,
            "It's an upright piano move from Brooklyn to Queens. What would you charge?",
            decision["advice_id"],
        )

        decision = advise(
            client,
            call_id,
            "It is flat. Labor is $400 and the total is $400.",
        )
        record(
            client,
            call_id,
            "Does the four hundred cover every charge?",
            decision["advice_id"],
        )
        assert len(decision["call"]["original_quote"]["line_items"]) == 1

        decision = advise(
            client,
            call_id,
            "There are no extra fees. It is binding and we are available August 1.",
        )
        record(
            client,
            call_id,
            "So the binding total is four hundred, all-in, for August first. Is that right?",
            decision["advice_id"],
        )

        decision = advise(client, call_id, "Yes, that's right.")
        assert decision["recommended_outcome"] == "complete_quote"
        finished = record(
            client,
            call_id,
            "Perfect. I've recorded the quote for the buyer. Thank you.",
            decision["advice_id"],
        )

    assert finished["terminal"] is True
    assert finished["call"]["outcome"]["outcome_type"] == "complete_quote"
    transcript = finished["call"]["transcript"]
    assert [event["speaker"] for event in transcript] == [
        "agent",
        "vendor",
        "agent",
        "vendor",
        "agent",
        "vendor",
        "agent",
        "vendor",
        "agent",
    ]
    for item in finished["call"]["original_quote"]["line_items"]:
        assert item["evidence"]["transcript_event_id"]


def test_agent_response_after_vendor_turn_requires_matching_gpt_advice(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "advice-token.db"),
        buyer_model=FakeAdvisor(),
        caller_agent_adapter=FakeCallerAgentAdapter(),
    )
    with TestClient(app) as client:
        started = post(client, "/api/v1/demo/agent/sessions", {})
        call_id = started["call"]["call"]["call_id"]
        advise(client, call_id, "Yes.")
        response = client.post(
            f"/api/v1/demo/agent/sessions/{call_id}/utterances",
            json={"spoken_text": "Here are the job details."},
        )

    assert response.status_code == 422
    assert "advice_id" in response.json()["detail"]
