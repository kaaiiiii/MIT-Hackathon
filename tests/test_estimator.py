import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.dialogue_planner import DialogueAction
from apps.api.app.caller.input_gateway import (
    InMemoryCallerInputGateway,
    SQLiteCallerInputGateway,
)
from apps.api.app.caller.orchestrator import specification_sha256
from apps.api.app.caller.openai_agent import BuyerTurnDecision
from apps.api.app.caller.schemas import VendorTarget
from apps.api.app.estimator.adapters import InMemoryCatalogResolver
from apps.api.app.estimator.schemas import CatalogCandidate


REQUIRED_MOVING_FIELDS = {
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


class _HandoffBuyerModel:
    model_name = "handoff-test-model"

    async def opening(self, view, job_facts=None):
        return "Hi, I'm an AI assistant. Is now a good time to discuss a quote?"

    async def respond(self, view, vendor_text, job_facts=None):
        facts = job_facts or {}
        return BuyerTurnDecision(
            planned_action=DialogueAction.PRESENT_JOB,
            spoken_response=(
                f"The move is from {facts['origin.location']} to "
                f"{facts['destination.location']}. What would you charge?"
            ),
        )


def _document(fields: dict[str, str], confidence: float = 0.99) -> bytes:
    return json.dumps(
        {
            "fields": [
                {
                    "field_name": name,
                    "value": value,
                    "region": {"page": 1, "line": index + 1, "bbox": [0, index, 10, index + 1]},
                    "confidence_score": confidence,
                }
                for index, (name, value) in enumerate(fields.items())
            ]
        }
    ).encode()


def _create_session(client: TestClient) -> dict:
    response = client.post("/api/v1/intake/sessions", json={"vertical": "moving"})
    assert response.status_code == 201, response.text
    return response.json()


def test_document_to_confirmed_spec_and_caller_handoff(tmp_path):
    vendor = VendorTarget(vendor_id="vendor_1", name="Mover One")
    app = create_app(
        database_path=str(tmp_path / "estimator.db"),
        inputs=InMemoryCallerInputGateway(vendors=[vendor]),
    )
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = session["session_id"]
        assert app.state.estimator_store.shared_version_count() == 0

        upload = client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={
                "document": (
                    "inventory.json",
                    _document(REQUIRED_MOVING_FIELDS),
                    "application/json",
                )
            },
        )
        assert upload.status_code == 200, upload.text
        assert upload.json()["session"]["status"] == "awaiting_confirmation"
        assert app.state.estimator_store.shared_version_count() == 0

        confirmed = client.post(
            f"/api/v1/intake/sessions/{session_id}/confirm",
            json={"approved": True, "confirmed_by": "user_123"},
        )
        assert confirmed.status_code == 200, confirmed.text
        body = confirmed.json()
        assert body["status"] == "confirmed"
        assert app.state.estimator_store.shared_version_count() == 1

        gateway = SQLiteCallerInputGateway(app.state.caller_store.connection)
        caller_spec = gateway.get_confirmed_job_spec(body["version_id"])
        assert caller_spec is not None
        assert specification_sha256(caller_spec) == body["canonical_hash"]

        created_call = client.post(
            "/api/v1/calls",
            json={
                "job_spec_version_id": body["version_id"],
                "vendor_id": "vendor_1",
            },
        )
        assert created_call.status_code == 201, created_call.text
        assert created_call.json()["spec_sha256"] == body["canonical_hash"]

        immutable = client.post(
            f"/api/v1/intake/sessions/{session_id}/voice",
            json={
                "turn_id": "turn_after_confirm",
                "user_text": "Actually it is a grand piano",
                "field_name": "item.type",
                "value": "grand piano",
            },
        )
        assert immutable.status_code == 409


def test_confirmation_refuses_missing_provenance_and_does_not_write_handoff(tmp_path):
    app = create_app(database_path=str(tmp_path / "incomplete.db"))
    with TestClient(app) as client:
        session = _create_session(client)
        response = client.post(
            f"/api/v1/intake/sessions/{session['session_id']}/confirm",
            json={"approved": True, "confirmed_by": "user_123"},
        )
        assert response.status_code == 422
        assert app.state.estimator_store.shared_version_count() == 0

        invented = client.post(
            f"/api/v1/intake/sessions/{session['session_id']}/voice",
            json={
                "turn_id": "turn_1",
                "user_text": "I have a piano",
                "field_name": "item.weight",
                "value": "500 pounds",
            },
        )
        assert invented.status_code == 422
        assert "not present" in invented.json()["detail"]


def test_unknown_requires_explicit_statement_and_acknowledgement(tmp_path):
    app = create_app(database_path=str(tmp_path / "unknown.db"))
    with TestClient(app) as client:
        session = _create_session(client)
        session_id = session["session_id"]
        first = client.post(f"/api/v1/intake/sessions/{session_id}/voice")
        assert first.status_code == 200
        assert first.json()["next_field"] == "service"
        second = client.post(f"/api/v1/intake/sessions/{session_id}/voice")
        assert "record it as unknown" in second.json()["spoken_question"]

        unknown = client.post(
            f"/api/v1/intake/sessions/{session_id}/voice",
            json={
                "turn_id": "turn_unknown",
                "user_text": "I don't know",
                "field_name": "service",
                "mark_unknown": True,
                "unknown_acknowledged": True,
            },
        )
        assert unknown.status_code == 200, unknown.text
        field = unknown.json()["session"]["fields"]["service"]
        assert field["value"] == "unknown"
        assert field["confidence"] == "unknown"
        assert unknown.json()["next_field"] == "origin.location"


def test_document_conflict_and_low_confidence_require_user_selection(tmp_path):
    app = create_app(database_path=str(tmp_path / "conflict.db"))
    with TestClient(app) as client:
        session_id = _create_session(client)["session_id"]
        first = client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={"document": ("one.json", _document({"item.type": "upright piano"}), "application/json")},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={"document": ("two.json", _document({"item.type": "grand piano"}, 0.5), "application/json")},
        )
        assert second.status_code == 200
        view = second.json()["session"]
        assert "item.type" in view["unresolved_conflicts"]
        assert "item.type" in second.json()["review_required_fields"]
        candidates = view["evidence_candidates"]["item.type"]
        grand = next(
            item for item in candidates if item["evidence"]["value"] == "grand piano"
        )
        assert grand["selected"] is False


def test_catalog_resolution_never_auto_selects(tmp_path):
    candidate = CatalogCandidate(
        candidate_id="kivik_gray",
        label="KIVIK 3-seat, Kelinge gray",
        canonical_value="IKEA KIVIK 3-seat Kelinge gray",
    )
    resolver = InMemoryCatalogResolver(
        {("furniture_catalog", "gray ikea sectional"): [candidate]}
    )
    app = create_app(
        database_path=str(tmp_path / "catalog.db"),
        estimator_catalog_resolver=resolver,
    )
    with TestClient(app) as client:
        session_id = _create_session(client)["session_id"]
        started = client.post(
            f"/api/v1/intake/sessions/{session_id}/resolve",
            json={
                "field_name": "item.catalog_identity",
                "raw_user_statement": "gray IKEA sectional",
                "catalog_name": "furniture_catalog",
            },
        )
        assert started.status_code == 200, started.text
        resolution = started.json()
        assert resolution["status"] == "pending"
        view = client.get(f"/api/v1/intake/sessions/{session_id}").json()
        assert view["status"] == "resolving"
        assert "item.catalog_identity" not in view["fields"]

        selected = client.post(
            f"/api/v1/intake/sessions/{session_id}/resolve",
            json={
                "resolution_id": resolution["resolution_id"],
                "selected_candidate_id": "kivik_gray",
            },
        )
        assert selected.status_code == 200, selected.text
        field = selected.json()["fields"]["item.catalog_identity"]
        assert field["resolution"]["selected_by"] == "user_confirmation"
        assert field["value"] == "IKEA KIVIK 3-seat Kelinge gray"


def test_edit_creates_new_version_without_invalidating_original_handoff(tmp_path):
    app = create_app(database_path=str(tmp_path / "versions.db"))
    with TestClient(app) as client:
        first_session = _create_session(client)
        first_id = first_session["session_id"]
        client.post(
            f"/api/v1/intake/sessions/{first_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={
                "document": (
                    "original.json",
                    _document(REQUIRED_MOVING_FIELDS),
                    "application/json",
                )
            },
        )
        first = client.post(
            f"/api/v1/intake/sessions/{first_id}/confirm",
            json={"approved": True, "confirmed_by": "user_123"},
        ).json()

        edited_session = client.post(
            "/api/v1/intake/sessions",
            json={"vertical": "moving", "base_version_id": first["version_id"]},
        )
        assert edited_session.status_code == 201, edited_session.text
        edited_id = edited_session.json()["session_id"]
        changed = client.post(
            f"/api/v1/intake/sessions/{edited_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={
                "document": (
                    "correction.json",
                    _document({"item.weight": "550 pounds"}),
                    "application/json",
                )
            },
        ).json()
        candidate = next(
            item
            for item in changed["session"]["evidence_candidates"]["item.weight"]
            if item["evidence"]["value"] == "550 pounds"
        )
        second = client.post(
            f"/api/v1/intake/sessions/{edited_id}/confirm",
            json={
                "approved": True,
                "confirmed_by": "user_123",
                "evidence_selections": [
                    {
                        "field_name": "item.weight",
                        "evidence_id": candidate["evidence_id"],
                    }
                ],
            },
        )
        assert second.status_code == 200, second.text
        assert second.json()["version_id"] != first["version_id"]

        shared = SQLiteCallerInputGateway(app.state.caller_store.connection)
        assert shared.get_confirmed_job_spec(first["version_id"]) is not None
        assert shared.get_confirmed_job_spec(second.json()["version_id"]) is not None
        old_metadata = app.state.estimator_store.get_confirmed_row(first["version_id"])
        assert old_metadata["status"] == "superseded"
        assert old_metadata["superseded_by"] == second.json()["version_id"]


def test_benchmark_reference_rejects_inline_values(tmp_path):
    app = create_app(database_path=str(tmp_path / "benchmark.db"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/intake/sessions",
            json={
                "vertical": "moving",
                "benchmark_refs": [
                    {
                        "field": "item.catalog_identity",
                        "benchmark_source": "ikea.com/us",
                        "benchmark_key": "KIVIK-3s-kelinge-gray",
                        "attached_at": "2026-07-18T12:00:00Z",
                        "price": 1299,
                    }
                ],
            },
        )
        assert response.status_code == 422


def test_vertical_swap_uses_yaml_without_estimator_code_changes(tmp_path):
    app = create_app(database_path=str(tmp_path / "vertical-swap.db"))
    fields = {
        "vehicle.year_make_model": "2020 Toyota Camry",
        "repair.scope": "replace front bumper",
        "vehicle.location": "Brooklyn, New York",
        "requested_date": "August 15, 2026",
    }
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/intake/sessions", json={"vertical": "auto_body"}
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["session_id"]
        uploaded = client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "repair_order_json"},
            files={
                "document": (
                    "repair.json",
                    _document(fields),
                    "application/json",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["session"]["status"] == "awaiting_confirmation"
        confirmed = client.post(
            f"/api/v1/intake/sessions/{session_id}/confirm",
            json={"approved": True, "confirmed_by": "user_456"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["vertical"] == "auto_body"


def test_estimator_lab_is_served_without_swagger_or_manual_json(tmp_path):
    app = create_app(database_path=str(tmp_path / "estimator-lab.db"))
    with TestClient(app) as client:
        page = client.get("/demo/estimator.html")
        script = client.get("/demo/estimator.js")
        assert page.status_code == 200
        assert "Build evidence-backed draft" in page.text
        assert script.status_code == 200
        assert "/api/v1/intake/sessions" in script.text


def test_confirmed_estimator_spec_can_start_caller_lab_session(tmp_path):
    app = create_app(
        database_path=str(tmp_path / "lab-handoff.db"),
        buyer_model=_HandoffBuyerModel(),
    )
    with TestClient(app) as client:
        session_id = _create_session(client)["session_id"]
        uploaded = client.post(
            f"/api/v1/intake/sessions/{session_id}/documents",
            data={"document_type": "moving_inventory_json"},
            files={
                "document": (
                    "handoff.json",
                    _document(REQUIRED_MOVING_FIELDS),
                    "application/json",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        confirmed = client.post(
            f"/api/v1/intake/sessions/{session_id}/confirm",
            json={"approved": True, "confirmed_by": "browser_user"},
        ).json()

        evidence_preview = client.get(
            f"/api/v1/intake/specs/{confirmed['version_id']}"
        )
        assert evidence_preview.status_code == 200, evidence_preview.text
        preview_field = evidence_preview.json()["fields"]["origin.location"]
        assert preview_field["value"] == "Brooklyn, New York"
        assert preview_field["source"]["modality"] == "document"
        assert preview_field["source"]["reference"]["region"]["line"] == 2
        assert preview_field["confidence"] == "explicit"

        started = client.post(
            "/api/v1/demo/sessions",
            json={"job_spec_version_id": confirmed["version_id"]},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["call"]["call"]["job_spec_version_id"] == confirmed["version_id"]
        assert body["call"]["call"]["spec_sha256"] == confirmed["canonical_hash"]
        assert body["confirmed_job_facts"]["origin.location"] == "Brooklyn, New York"

        call_id = body["call"]["call"]["call_id"]
        presented = client.post(
            f"/api/v1/demo/sessions/{call_id}/messages",
            json={"text": "Yes, I can discuss the job."},
        )
        assert presented.status_code == 200, presented.text
        assert "Brooklyn, New York" in presented.json()["agent_message"]
        assert "Queens, New York" in presented.json()["agent_message"]
