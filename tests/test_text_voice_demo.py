from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.dialogue_planner import DialogueAction
from apps.api.app.caller.openai_agent import (
    BuyerTurnDecision,
    ExtractedLineItem,
    ExtractedTerm,
)
from apps.api.app.caller.audio import SynthesizedAudio


class FakeBuyerModel:
    model_name = "gpt-5.4-test"

    async def opening(self, view, job_facts=None):
        return (
            "Hi, I'm an AI assistant calling for a buyer. "
            "Is now a good time to discuss a quote?"
        )

    async def respond(self, view, vendor_text, job_facts=None):
        lowered = vendor_text.casefold()
        if view.call.status.value == "disclosure":
            if "call you back" in lowered:
                return BuyerTurnDecision(
                    intent="callback",
                    planned_action=DialogueAction.CONTINUE,
                    spoken_response="I'll record the callback commitment. Thank you.",
                )
            facts = job_facts or {}
            origin = facts.get("origin.location") or facts.get("origin") or "Brooklyn"
            destination = (
                facts.get("destination.location")
                or facts.get("destination")
                or "Queens"
            )
            return BuyerTurnDecision(
                planned_action=DialogueAction.PRESENT_JOB,
                spoken_response=(
                    f"The confirmed piano move is from {origin} to {destination}. "
                    "What would you charge?"
                ),
            )
        if "all-inclusive" in vendor_text:
            return BuyerTurnDecision(
                planned_action=DialogueAction.CLARIFY_FEES,
                spoken_response="Does that $640 include every fee?",
                line_items=[
                    ExtractedLineItem(
                        category="labor", description="All-inclusive moving labor", amount=640
                    )
                ],
                terms=[
                    ExtractedTerm(
                        category="pricing_model",
                        key="pricing_model",
                        value_text="flat",
                    ),
                    ExtractedTerm(
                        category="estimated_total",
                        key="total",
                        value_number=640,
                    ),
                ],
            )
        if "labor is $350" in lowered and "total is $475" in lowered:
            return BuyerTurnDecision(
                planned_action=DialogueAction.CLARIFY_FEES,
                spoken_response="Does that $475 include every fee?",
                line_items=[
                    ExtractedLineItem(
                        category="labor", description="Moving labor", amount=350
                    ),
                    ExtractedLineItem(
                        category="stairs", description="Stair charge", amount=75
                    ),
                ],
                terms=[
                    ExtractedTerm(
                        category="pricing_model",
                        key="pricing_model",
                        value_text="flat",
                    ),
                    ExtractedTerm(
                        category="estimated_total", key="total", value_number=475
                    ),
                ],
            )
        if "flat price" in lowered or "use a flat" in lowered:
            return BuyerTurnDecision(
                planned_action=DialogueAction.REQUEST_ITEMIZATION,
                terms=[
                    ExtractedTerm(
                        category="pricing_model",
                        key="pricing_model",
                        value_text="flat",
                    )
                ],
                spoken_response="How does that price break down by charge?",
            )
        if "not to disclose" in lowered or "keep it as a package" in lowered:
            return BuyerTurnDecision(
                response_relation="refused",
                planned_action=DialogueAction.REQUEST_TOTAL,
                terms=[
                    ExtractedTerm(
                        category="itemization_status",
                        key="vendor_itemization_refusal",
                        value_text="refused",
                    )
                ],
                spoken_response="That's okay. What is the bundled total?",
            )
        if "bundled total is $500" in lowered:
            return BuyerTurnDecision(
                planned_action=DialogueAction.CLARIFY_FEES,
                terms=[
                    ExtractedTerm(
                        category="estimated_total", key="total", value_number=500
                    )
                ],
                spoken_response="Does that $500 include every fee?",
            )
        if (
            "no additional fees" in lowered
            and "binding" in lowered
            and "available" in lowered
        ):
            terms = [
                ExtractedTerm(
                    category="fee",
                    key="additional_fees",
                    value_text="No additional fees stated",
                ),
                ExtractedTerm(
                    category="binding_status",
                    key="binding_status",
                    value_text="binding",
                ),
                ExtractedTerm(
                    category="availability",
                    key="vendor_availability",
                    value_text="Available August 1",
                ),
            ]
            if view.call.policy.approved_leverage_bid_id:
                bid = view.call.policy.verified_competing_bids[0]
                return BuyerTurnDecision(
                    planned_action=DialogueAction.USE_VERIFIED_LEVERAGE,
                    terms=terms,
                    spoken_response=(
                        f"I have a verified competing quote for ${bid.total:,.2f} "
                        "for the same job. What can you do on the price?"
                    ),
                )
            total = next(
                (
                    term.value
                    for term in reversed(view.original_quote.terms)
                    if term.category == "estimated_total"
                ),
                None,
            )
            itemization = (
                "The vendor declined to itemize. "
                if any(
                    term.category == "itemization_status"
                    for term in view.original_quote.terms
                )
                else ""
            )
            return BuyerTurnDecision(
                planned_action=DialogueAction.CONFIRM_SUMMARY,
                terms=terms,
                spoken_response=(
                    f"{itemization}The total is ${float(total):,.2f}, binding and "
                    "available August 1. Is that accurate?"
                ),
            )
        if "lower the total to $450" in lowered:
            return BuyerTurnDecision(
                planned_action=DialogueAction.CONFIRM_SUMMARY,
                terms=[
                    ExtractedTerm(
                        category="estimated_total", key="revised_total", value_number=450
                    )
                ],
                spoken_response=(
                    "The revised total is $450.00, binding with no extra fees. "
                    "Is that accurate?"
                ),
            )
        if view.call.status.value == "summary_confirmation":
            return BuyerTurnDecision(
                intent="confirm_summary",
                planned_action=DialogueAction.CONFIRM_SUMMARY,
                spoken_response="Thank you. I've finalized the evidence-backed quote.",
            )
        return BuyerTurnDecision(
            planned_action=DialogueAction.CONTINUE,
            spoken_response="Could you clarify the quote?",
        )

class FakeAudioAdapter:
    def __init__(self):
        self.transcriptions = ["Yes, I can discuss it."]
        self.synthesized: list[str] = []
        self.streamed: list[str] = []

    async def transcribe(self, audio, *, filename, media_type):
        assert audio == b"recorded-vendor-audio"
        assert filename == "vendor.webm"
        assert media_type == "audio/webm"
        return self.transcriptions.pop(0)

    async def synthesize(self, text):
        self.synthesized.append(text)
        return SynthesizedAudio(b"generated-buyer-mp3", "audio/mpeg")

    async def synthesize_stream(self, text):
        self.streamed.append(text)
        yield b"streamed-"
        yield b"buyer-mp3"

    async def close(self):
        pass


def send(client: TestClient, call_id: str, text: str):
    response = client.post(
        f"/api/v1/demo/sessions/{call_id}/messages", json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_text_voice_demo_completes_evidence_backed_quote(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "demo.db"), buyer_model=FakeBuyerModel()
    )
    with TestClient(app) as client:
        started = client.post("/api/v1/demo/sessions")
        assert started.status_code == 200
        body = started.json()
        call_id = body["call"]["call"]["call_id"]
        assert body["call"]["call"]["status"] == "disclosure"

        body = send(client, call_id, "Yes, I can discuss it.")
        assert body["call"]["call"]["status"] == "quote_collection"

        body = send(
            client,
            call_id,
            "We charge a flat price. Labor is $350, stairs are $75, and the total is $475.",
        )
        assert body["call"]["call"]["status"] == "quote_clarification"
        assert len(body["call"]["original_quote"]["line_items"]) == 2

        body = send(
            client,
            call_id,
            "There are no additional fees. The total is binding, and we are available August 1.",
        )
        assert body["call"]["call"]["status"] == "summary_confirmation"
        assert "$475.00" in body["agent_message"]

        body = send(client, call_id, "Yes, that's correct.")
        assert body["terminal"] is True
        assert body["call"]["outcome"]["outcome_type"] == "complete_quote"
        assert body["call"]["original_quote"]["status"] == "final"
        for item in body["call"]["original_quote"]["line_items"]:
            assert item["evidence"]["transcript_event_id"]


def test_text_voice_demo_records_callback(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "callback.db"), buyer_model=FakeBuyerModel()
    )
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        body = send(client, call_id, "I will call you back tomorrow morning.")
        assert body["terminal"] is True
        assert body["call"]["outcome"]["outcome_type"] == "callback_required"


def test_itemization_refusal_is_recorded_once_and_advances_the_call(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "itemization-refusal.db"),
        buyer_model=FakeBuyerModel(),
    )
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        send(client, call_id, "Yes, I can discuss it.")

        body = send(client, call_id, "It is a flat price.")
        assert "break down" in body["agent_message"].casefold()

        body = send(client, call_id, "I prefer not to disclose the breakdown.")
        response = body["agent_message"].casefold()
        assert "total" in response
        assert "itemize" not in response
        assert "main cost" not in response
        assert "cost category" not in response
        refusal_terms = [
            term
            for term in body["call"]["original_quote"]["terms"]
            if term["category"] == "itemization_status"
        ]
        assert len(refusal_terms) == 1
        assert refusal_terms[0]["value"] == "refused"
        body = send(client, call_id, "The bundled total is $500.")
        assert body["call"]["call"]["status"] == "quote_clarification"
        body = send(
            client,
            call_id,
            "There are no additional fees. The total is binding, and we are available August 1.",
        )
        assert body["call"]["call"]["status"] == "summary_confirmation"
        assert "declined to itemize" in body["agent_message"].casefold()

        body = send(client, call_id, "Yes, that is accurate.")
        assert body["terminal"] is True
        assert body["call"]["outcome"]["outcome_type"] == "incomplete_quote"
        assert "Vendor declined to itemize the quote" in (
            body["call"]["outcome"]["validation_warnings"]
        )


def test_caller_records_itemization_action_only_once(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "one-itemization-request.db"),
        buyer_model=FakeBuyerModel(),
    )
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        send(client, call_id, "Yes, I can discuss it.")

        body = send(client, call_id, "We use a flat price.")
        assert "break down" in body["agent_message"].casefold()

        body = send(client, call_id, "We keep it as a package.")
        reply = body["agent_message"].casefold()
        assert "total" in reply
        assert "break down" not in reply
        assert "itemize" not in reply


def test_demo_page_is_served(tmp_path):
    app = create_app(database_path=str(tmp_path / "page.db"))
    with TestClient(app) as client:
        response = client.get("/demo/")
        assert response.status_code == 200
        assert "Speak as the vendor" in response.text
        assert "/demo/agent-app.js" in response.text


def test_injected_gpt_model_handles_custom_vendor_language(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "gpt.db"), buyer_model=FakeBuyerModel()
    )
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        body = send(client, call_id, "Sure, go ahead with the details.")
        assert body["model"] == "gpt-5.4-test"
        body = send(
            client,
            call_id,
            "Our concierge move is all-inclusive at six hundred forty dollars.",
        )
        assert body["call"]["call"]["status"] == "quote_clarification"
        quote = body["call"]["original_quote"]
        assert quote["line_items"][0]["amount"] == 640
        assert any(
            term["category"] == "estimated_total" and term["value"] == 640
            for term in quote["terms"]
        )


def test_voice_pipeline_transcribes_runs_turn_and_synthesizes(tmp_path):
    audio = FakeAudioAdapter()
    app = create_app(
        database_path=str(tmp_path / "voice.db"),
        buyer_model=FakeBuyerModel(),
        audio_adapter=audio,
    )
    with TestClient(app) as client:
        started = client.post("/api/v1/demo/voice/sessions")
        assert started.status_code == 200, started.text
        start_body = started.json()
        call_id = start_body["call"]["call"]["call_id"]
        assert start_body["audio_content_type"] == "audio/mpeg"
        assert start_body["audio_base64"]

        response = client.post(
            f"/api/v1/demo/voice/sessions/{call_id}/messages",
            files={"audio": ("vendor.webm", b"recorded-vendor-audio", "audio/webm")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["transcription"] == "Yes, I can discuss it."
        assert body["model"] == "gpt-5.4-test"
        assert body["call"]["call"]["status"] == "quote_collection"
        assert body["call"]["transcript"][-2]["text"] == "Yes, I can discuss it."
        assert body["audio_base64"]
        assert len(audio.synthesized) == 2


def test_voice_stream_mode_defers_tts_and_streams_speech(tmp_path):
    audio = FakeAudioAdapter()
    app = create_app(
        database_path=str(tmp_path / "voice-stream.db"),
        buyer_model=FakeBuyerModel(),
        audio_adapter=audio,
    )
    with TestClient(app) as client:
        started = client.post("/api/v1/demo/voice/sessions?tts=stream")
        assert started.status_code == 200, started.text
        body = started.json()
        call_id = body["call"]["call"]["call_id"]
        assert body["audio_base64"] == ""
        assert body["audio_url"].startswith(
            f"/api/v1/demo/voice/sessions/{call_id}/speech"
        )
        assert audio.synthesized == []

        greeting = client.get(body["audio_url"])
        assert greeting.status_code == 200
        assert greeting.content == b"streamed-buyer-mp3"
        assert greeting.headers["content-type"].startswith("audio/mpeg")
        assert audio.streamed == [body["agent_message"]]

        turn = client.post(
            f"/api/v1/demo/voice/sessions/{call_id}/text?tts=stream",
            json={"text": "Sure, go ahead with the details."},
        ).json()
        speech = client.get(turn["audio_url"])
        assert speech.status_code == 200
        assert speech.content == b"streamed-buyer-mp3"
        assert audio.streamed[-1] == turn["agent_message"]
        assert audio.synthesized == []


def test_voice_endpoint_explains_missing_elevenlabs_configuration(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "no-voice.db"), buyer_model=FakeBuyerModel()
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/demo/voice/sessions")
        assert response.status_code == 502
        assert "ELEVENLABS_API_KEY" in response.json()["detail"]


def test_demo_tracks_both_parties_and_uses_only_verified_leverage(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "leverage.db"), buyer_model=FakeBuyerModel()
    )
    verified_bid = {
        "bid_id": "bid_verified_425",
        "source_call_id": "call_previous_vendor",
        "job_spec_version_id": "demo_spec_piano",
        "total": 425,
        "currency": "USD",
        "binding_status": "binding",
        "evidence_reference": "transcript://call_previous_vendor/te_42",
    }
    with TestClient(app) as client:
        started = client.post(
            "/api/v1/demo/sessions",
            json={"verified_competing_bid": verified_bid},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        call_id = body["call"]["call"]["call_id"]

        body = send(client, call_id, "Yes, I can discuss it.")
        body = send(
            client,
            call_id,
            "We charge a flat price. Labor is $350, stairs are $75, and the total is $475.",
        )
        body = send(
            client,
            call_id,
            "There are no additional fees. The total is binding, and we are available August 1.",
        )

        assert body["call"]["call"]["status"] == "quote_clarification"
        assert "verified competing quote for $425.00" in body["agent_message"]
        assert body["call"]["call"]["policy"]["approved_leverage_bid_id"] == "bid_verified_425"
        speakers = {event["speaker"] for event in body["call"]["transcript"]}
        assert speakers == {"agent", "vendor"}

        body = send(client, call_id, "We can lower the total to $450.")
        assert body["call"]["call"]["status"] == "summary_confirmation"
        assert "$450.00" in body["agent_message"]
        assert sum(
            "verified competing quote" in event["text"]
            for event in body["call"]["transcript"]
        ) == 1
