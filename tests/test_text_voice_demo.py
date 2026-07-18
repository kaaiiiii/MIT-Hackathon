from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.openai_agent import (
    BuyerTurnDecision,
    ExtractedLineItem,
    ExtractedTerm,
)
from apps.api.app.caller.audio import SynthesizedAudio


class FakeBuyerModel:
    model_name = "gpt-5.4-test"

    async def respond(self, view, vendor_text, job_facts=None):
        if view.call.status.value == "disclosure":
            return BuyerTurnDecision(
                spoken_response="Thanks. The confirmed piano move is from Brooklyn to Queens. What is your pricing?"
            )
        if "all-inclusive" in vendor_text:
            return BuyerTurnDecision(
                spoken_response="Thanks. Are there any additional fees, and is that total binding?",
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
        return BuyerTurnDecision(spoken_response="Could you clarify the quote?")

class FakeAudioAdapter:
    def __init__(self):
        self.transcriptions = ["Yes, I can discuss it."]
        self.synthesized: list[str] = []

    async def transcribe(self, audio, *, filename, media_type):
        assert audio == b"recorded-vendor-audio"
        assert filename == "vendor.webm"
        assert media_type == "audio/webm"
        return self.transcriptions.pop(0)

    async def synthesize(self, text):
        self.synthesized.append(text)
        return SynthesizedAudio(b"generated-buyer-mp3", "audio/mpeg")

    async def close(self):
        pass


def send(client: TestClient, call_id: str, text: str):
    response = client.post(
        f"/api/v1/demo/sessions/{call_id}/messages", json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_text_voice_demo_completes_evidence_backed_quote(tmp_path):
    app = create_app(database_path=str(tmp_path / "demo.db"))
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
    app = create_app(database_path=str(tmp_path / "callback.db"))
    with TestClient(app) as client:
        body = client.post("/api/v1/demo/sessions").json()
        call_id = body["call"]["call"]["call_id"]
        body = send(client, call_id, "I will call you back tomorrow morning.")
        assert body["terminal"] is True
        assert body["call"]["outcome"]["outcome_type"] == "callback_required"


def test_demo_page_is_served(tmp_path):
    app = create_app(database_path=str(tmp_path / "page.db"))
    with TestClient(app) as client:
        response = client.get("/demo/")
        assert response.status_code == 200
        assert "Speak as the vendor" in response.text
        assert "/demo/app.js" in response.text


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


def test_voice_endpoint_explains_missing_elevenlabs_configuration(tmp_path):
    app = create_app(database_path=str(tmp_path / "no-voice.db"))
    with TestClient(app) as client:
        response = client.post("/api/v1/demo/voice/sessions")
        assert response.status_code == 502
        assert "ELEVENLABS_API_KEY" in response.json()["detail"]


def test_demo_tracks_both_parties_and_uses_only_verified_leverage(tmp_path):
    app = create_app(database_path=str(tmp_path / "leverage.db"))
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
