import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.input_gateway import InMemoryCallerInputGateway
from apps.api.app.caller.schemas import VendorTarget
from apps.api.app.research.openai_researcher import (
    OpenAIContextResearcher,
    ResearchResult,
)
from apps.api.app.research.schemas import (
    ConversationOpportunity,
    ResearchArtifact,
    ResearchClaim,
    ResearchSource,
)


FIELDS = {
    "service": "Move one upright piano",
    "origin.location": "Brooklyn, New York",
    "destination.location": "Queens, New York",
    "requested_date": "August 1, 2026",
    "item.type": "upright piano",
    "item.dimensions": "58 by 24 by 48 inches",
    "item.weight": "500 pounds",
    "origin.access": "first floor",
    "destination.access": "second floor with one flight",
}


class FakeResearcher:
    model_name = "fake-best-model"

    def __init__(self):
        self.requests = []

    async def research(self, *, stage, payload):
        self.requests.append((stage, payload))
        return ResearchResult(
            artifact=ResearchArtifact(
                topic_summary=f"Grounded context for {stage}",
                terminology=["flight charge"],
                risk_factors=["access fees should be confirmed"],
                assumptions_to_verify=["whether insurance is included"],
                suggested_vendor_questions=["What could change this total?"],
                likely_fee_categories=["stairs", "travel"],
                conversation_opportunities=[
                    ConversationOpportunity(
                        objective="Ask how access affects the quote",
                        rationale="Access can affect handling requirements.",
                        allowed_use="frame_confirmed_fact",
                        confirmed_field_names=[
                            "origin.access",
                            "destination.access",
                        ],
                        source_urls=["https://example.test/source"],
                    )
                ],
                claims=[
                    ResearchClaim(
                        claim="Specialty moves may have access-related charges.",
                        relevance="Ask vendors to state stair charges explicitly.",
                        source_urls=["https://example.test/source"],
                        confidence="high",
                    )
                ],
                sources=[
                    ResearchSource(
                        title="Authoritative test source",
                        url="https://example.test/source",
                    )
                ],
            ),
            response_id=f"response-{len(self.requests)}",
        )


class _FakeResponses:
    def __init__(self):
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        artifact = ResearchArtifact(topic_summary="Terra search result")
        return type(
            "ParsedResponse",
            (),
            {"output_parsed": artifact, "id": "response-terra"},
        )()


class _FakeOpenAIClient:
    def __init__(self):
        self.responses = _FakeResponses()


async def test_openai_research_defaults_to_terra_medium():
    client = _FakeOpenAIClient()
    researcher = OpenAIContextResearcher(api_key="test", client=client)

    result = await researcher.research(
        stage="final_report_research", payload={"confirmed_job_spec": {}}
    )

    assert researcher.model_name == "gpt-5.6-terra"
    assert client.responses.kwargs["model"] == "gpt-5.6-terra"
    assert client.responses.kwargs["reasoning"] == {"effort": "medium"}
    assert result.response_id == "response-terra"


def _document():
    return json.dumps(
        {
            "fields": [
                {
                    "field_name": name,
                    "value": value,
                    "region": {"page": 1, "line": index + 1},
                    "confidence_score": 0.99,
                }
                for index, (name, value) in enumerate(FIELDS.items())
            ]
        }
    ).encode()


def _finish_incomplete_call(client, version_id, vendor_id):
    created = client.post(
        "/api/v1/calls",
        json={"job_spec_version_id": version_id, "vendor_id": vendor_id},
    )
    assert created.status_code == 201, created.text
    call_id = created.json()["call_id"]
    started = client.post(f"/api/v1/calls/{call_id}/start")
    assert started.status_code == 200, started.text
    client.post(
        f"/api/v1/calls/{call_id}/events",
        json={"type": "connection_established"},
    )
    client.post(
        f"/api/v1/calls/{call_id}/events",
        json={"type": "phase_completed", "phase": "disclosure"},
    )
    client.post(
        f"/api/v1/calls/{call_id}/events",
        json={"type": "phase_completed", "phase": "job_presentation"},
    )
    transcript = client.post(
        f"/api/v1/calls/{call_id}/events",
        json={
            "type": "transcript",
            "transcript": {
                "speaker": "vendor",
                "text": "I need to call you back with a firm number.",
                "timestamp_seconds": 4.2,
                "sequence": 0,
            },
        },
    )
    assert transcript.status_code == 200, transcript.text
    finalized = client.post(
        f"/api/v1/calls/{call_id}/finalize",
        json={"requested_outcome": "incomplete_quote"},
    )
    assert finalized.status_code == 200, finalized.text
    return call_id, started.json()


def test_research_flows_from_intake_to_later_calls_and_report(tmp_path):
    researcher = FakeResearcher()
    vendors = [
        VendorTarget(vendor_id="vendor_1", name="Mover One"),
        VendorTarget(vendor_id="vendor_2", name="Mover Two"),
    ]
    app = create_app(
        database_path=str(tmp_path / "research.db"),
        inputs=InMemoryCallerInputGateway(vendors=vendors),
        context_researcher=researcher,
    )
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/intake/sessions", json={"vertical": "moving"}
        ).json()
        session_id = session["session_id"]
        upload = client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={"document": ("inventory.json", _document(), "application/json")},
        )
        assert upload.status_code == 200, upload.text

        confirmed = client.post(
            f"/api/v1/intake/sessions/{session_id}/confirm",
            json={"approved": True, "confirmed_by": "test-user"},
        )
        assert confirmed.status_code == 200, confirmed.text
        version_id = confirmed.json()["version_id"]

        first_id, first_start = _finish_incomplete_call(
            client, version_id, "vendor_1"
        )
        assert first_start["augmented_context"]["prior_completed_calls"] == []
        first_brief = first_start["augmented_context"]["pre_call_brief"]
        assert first_brief["likely_fee_categories"] == ["stairs", "travel"]
        assert first_brief["conversation_opportunities"][0][
            "confirmed_field_names"
        ] == ["origin.access", "destination.access"]

        second_id, second_start = _finish_incomplete_call(
            client, version_id, "vendor_2"
        )
        context = second_start["augmented_context"]
        assert context["estimator_research"]["model"] == "fake-best-model"
        assert [item["call_id"] for item in context["prior_completed_calls"]] == [
            first_id
        ]
        assert context["prior_completed_calls"][0]["transcript"][0]["text"].startswith(
            "I need"
        )

        prepared = client.post(
            f"/api/v1/reports/{version_id}/prepare",
            json={"call_ids": [first_id, second_id]},
        )
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["final_research"]["based_on_call_ids"] == [
            first_id,
            second_id,
        ]

        report_context = client.get(
            f"/api/v1/research/specs/{version_id}/report-context"
        )
        assert report_context.status_code == 200, report_context.text
        body = report_context.json()
        assert body["estimator_research"] is not None
        assert body["final_research"] is not None
        assert len(body["completed_calls"]) == 2
        assert [request[0] for request in researcher.requests] == [
            "estimator_enrichment",
            "final_report_research",
        ]


def test_final_research_rejects_nonterminal_calls(tmp_path):
    researcher = FakeResearcher()
    vendor = VendorTarget(vendor_id="vendor_1", name="Mover One")
    app = create_app(
        database_path=str(tmp_path / "unfinished.db"),
        inputs=InMemoryCallerInputGateway(vendors=[vendor]),
        context_researcher=researcher,
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/intake/sessions", json={"vertical": "moving"}
        ).json()["session_id"]
        client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={"document": ("inventory.json", _document(), "application/json")},
        )
        version_id = client.post(
            f"/api/v1/intake/sessions/{session_id}/confirm",
            json={"approved": True, "confirmed_by": "test-user"},
        ).json()["version_id"]
        call_id = client.post(
            "/api/v1/calls",
            json={"job_spec_version_id": version_id, "vendor_id": "vendor_1"},
        ).json()["call_id"]
        response = client.post(
            f"/api/v1/research/specs/{version_id}/final",
            json={"call_ids": [call_id]},
        )
        assert response.status_code == 422
        assert "terminal" in response.json()["detail"]
