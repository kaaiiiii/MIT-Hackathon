from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.audio import SynthesizedAudio
from apps.api.app.samples.generator import (
    AnalyzedQuote,
    SampleAnalysis,
    SyntheticSample,
)


class FakeAudioAdapter:
    def __init__(self):
        self.synthesized: list[str] = []

    async def transcribe(self, audio, *, filename, media_type):
        return "Hi, this is Beacon Movers. Flat rate, the total is $480, binding."

    async def synthesize(self, text):
        self.synthesized.append(text)
        return SynthesizedAudio(b"synthetic-mp3", "audio/mpeg")

    async def synthesize_stream(self, text):
        yield b"synthetic-mp3"

    async def close(self):
        pass


class FakeSampleGenerator:
    model_name = "gpt-5.4-test"

    def __init__(self):
        self.requested_counts: list[int] = []

    async def generate(self, *, count, recorded_transcripts):
        assert recorded_transcripts, "generator must receive recorded examples"
        self.requested_counts.append(count)
        return [
            SyntheticSample(
                agency_name=f"Synthetic Agency {index}",
                quote_total=400.0 + index,
                transcript=(
                    "Hello, you reached a moving company. For that piano job "
                    f"we charge a flat total of ${400 + index}, no hidden fees, "
                    "binding, and we are available on your date."
                ),
            )
            for index in range(count)
        ]

    async def analyze(self, *, quotes):
        assert quotes, "analyzer must receive the collected quotes"
        return SampleAnalysis(
            summary=f"Compared {len(quotes)} sample quotes.",
            lowest_quote_agency=quotes[0]["agency_name"],
            quotes=[
                AnalyzedQuote(
                    agency_name=quote["agency_name"],
                    quote_total=quote["stated_total"],
                    binding_status="binding",
                )
                for quote in quotes
            ],
            observations=["All sample quotes state binding totals."],
        )


def make_app(tmp_path, **overrides):
    return create_app(
        database_path=str(tmp_path / "samples.db"),
        samples_dir=str(tmp_path / "sample_calls"),
        audio_adapter=overrides.get("audio_adapter", FakeAudioAdapter()),
        sample_generator=overrides.get("sample_generator", FakeSampleGenerator()),
    )


def test_session_defaults_to_five_quotes(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = client.post("/api/v1/samples/sessions", json={}).json()
        assert body["target_quotes"] == 5
        assert body["remaining_slots"] == 5


def test_session_rejects_more_than_twenty_quotes_transparently(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        ok = client.post("/api/v1/samples/sessions", json={"target_quotes": 20})
        assert ok.status_code == 200

        over = client.post("/api/v1/samples/sessions", json={"target_quotes": 21})
        assert over.status_code == 422
        detail = over.json()["detail"]
        assert "up to 20" in detail
        assert "contact support" in detail


def test_recording_is_transcribed_and_stored_as_a_file(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        session = client.post("/api/v1/samples/sessions", json={}).json()
        session_id = session["session_id"]
        body = client.post(
            f"/api/v1/samples/sessions/{session_id}/recordings",
            files={"audio": ("sample.webm", b"agency-recording", "audio/webm")},
            data={"agency_name": "Beacon Movers"},
        ).json()
        assert body["recorded_count"] == 1
        recording = body["recordings"][0]
        assert recording["source"] == "recorded"
        assert recording["agency_name"] == "Beacon Movers"
        assert "$480" in recording["transcript"]
        assert recording["quote_total"] == 480.0
        assert recording["has_audio"] is True

        stored = list((tmp_path / "sample_calls").rglob("*.webm"))
        assert len(stored) == 1
        assert stored[0].read_bytes() == b"agency-recording"


def test_synthesize_requires_a_recorded_example_first(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        session = client.post("/api/v1/samples/sessions", json={}).json()
        response = client.post(
            f"/api/v1/samples/sessions/{session['session_id']}/synthesize"
        )
        assert response.status_code == 422
        assert "Record at least one" in response.json()["detail"]


def test_synthesize_fills_remaining_slots_with_audio(tmp_path):
    generator = FakeSampleGenerator()
    audio = FakeAudioAdapter()
    app = make_app(tmp_path, sample_generator=generator, audio_adapter=audio)
    with TestClient(app) as client:
        session = client.post("/api/v1/samples/sessions", json={}).json()
        session_id = session["session_id"]
        client.post(
            f"/api/v1/samples/sessions/{session_id}/recordings",
            files={"audio": ("sample.webm", b"agency-recording", "audio/webm")},
        )
        body = client.post(
            f"/api/v1/samples/sessions/{session_id}/synthesize"
        ).json()
        assert generator.requested_counts == [4]
        assert body["recorded_count"] == 1
        assert body["synthetic_count"] == 4
        assert body["remaining_slots"] == 0
        assert len(audio.synthesized) == 4

        synthetic = [r for r in body["recordings"] if r["source"] == "synthetic"]
        assert all(r["has_audio"] for r in synthetic)
        audio_response = client.get(
            f"/api/v1/samples/sessions/{session_id}/recordings/"
            f"{synthetic[0]['recording_id']}/audio"
        )
        assert audio_response.status_code == 200
        assert audio_response.content == b"synthetic-mp3"


def test_analysis_runs_and_persists_on_the_session(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        session = client.post(
            "/api/v1/samples/sessions", json={"target_quotes": 2}
        ).json()
        session_id = session["session_id"]

        blocked = client.post(f"/api/v1/samples/sessions/{session_id}/analyze")
        assert blocked.status_code == 422

        client.post(
            f"/api/v1/samples/sessions/{session_id}/recordings",
            files={"audio": ("sample.webm", b"agency-recording", "audio/webm")},
            data={"agency_name": "Beacon Movers"},
        )
        client.post(f"/api/v1/samples/sessions/{session_id}/synthesize")
        body = client.post(
            f"/api/v1/samples/sessions/{session_id}/analyze"
        ).json()
        analysis = body["analysis"]
        assert analysis["summary"] == "Compared 2 sample quotes."
        assert analysis["lowest_quote_agency"] == "Beacon Movers"
        assert len(analysis["quotes"]) == 2
        assert analysis["observations"]

        reloaded = client.get(f"/api/v1/samples/sessions/{session_id}").json()
        assert reloaded["analysis"]["summary"] == "Compared 2 sample quotes."


def test_sample_page_is_served_and_linked(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        page = client.get("/demo/samples.html")
        assert page.status_code == 200
        assert "Record sample calls" in page.text or "Sample Calls" in page.text
        home = client.get("/demo/")
        assert "/demo/samples.html" in home.text
