from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.caller.input_gateway import InMemoryCallerInputGateway


def test_create_start_and_read_call(tmp_path, confirmed_spec, vendor):
    app = create_app(
        database_path=str(tmp_path / "caller.db"),
        inputs=InMemoryCallerInputGateway([confirmed_spec], [vendor]),
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/calls",
            json={
                "job_spec_version_id": "spec_123",
                "vendor_id": "vendor_transparent",
                "call_type": "initial_quote",
            },
        )
        assert created.status_code == 201
        call_id = created.json()["call_id"]

        started = client.post(f"/api/v1/calls/{call_id}/start")
        assert started.status_code == 200
        assert started.json()["immutable_spec_sha256"] == created.json()["spec_sha256"]

        connected = client.post(
            f"/api/v1/calls/{call_id}/events",
            json={"type": "connection_established"},
        )
        assert connected.status_code == 200
        assert connected.json()["status"] == "disclosure"

        view = client.get(f"/api/v1/calls/{call_id}")
        assert view.status_code == 200
        assert view.json()["call"]["job_spec_version_id"] == "spec_123"
        assert view.json()["original_quote"]["status"] == "draft"

