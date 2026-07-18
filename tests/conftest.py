from datetime import UTC, datetime

import pytest

from apps.api.app.caller.adapters.simulated import SimulatedVoiceSessionAdapter
from apps.api.app.caller.input_gateway import InMemoryCallerInputGateway
from apps.api.app.caller.orchestrator import CallOrchestrator
from apps.api.app.caller.persistence import SQLiteCallerStore
from apps.api.app.caller.schemas import ConfirmedJobSpec, VendorTarget


@pytest.fixture
def confirmed_spec() -> ConfirmedJobSpec:
    return ConfirmedJobSpec(
        version_id="spec_123",
        status="confirmed",
        facts={
            "service": "move a piano",
            "origin": "first floor",
            "destination": "second floor",
            "date": "2026-08-01",
        },
        confirmed_at=datetime.now(UTC),
    )


@pytest.fixture
def vendor() -> VendorTarget:
    return VendorTarget(
        vendor_id="vendor_transparent",
        name="Transparent Movers",
        phone="+12125550100",
    )


@pytest.fixture
def caller(confirmed_spec, vendor):
    store = SQLiteCallerStore(":memory:")
    inputs = InMemoryCallerInputGateway([confirmed_spec], [vendor])
    adapter = SimulatedVoiceSessionAdapter()
    service = CallOrchestrator(store, inputs, adapter)
    yield service
    store.close()

