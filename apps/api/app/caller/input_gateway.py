from __future__ import annotations

import json
import sqlite3

from .schemas import ConfirmedJobSpec, VendorTarget


class SQLiteCallerInputGateway:
    """Reads upstream-owned records; never mutates them."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def get_confirmed_job_spec(self, version_id: str) -> ConfirmedJobSpec | None:
        row = self.connection.execute(
            """
            SELECT version_id, status, facts_json, confirmed_at
            FROM job_spec_versions WHERE version_id = ? AND status = 'confirmed'
            """,
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return ConfirmedJobSpec(
            version_id=row[0], status=row[1], facts=json.loads(row[2]), confirmed_at=row[3]
        )

    def get_vendor_target(self, vendor_id: str) -> VendorTarget | None:
        row = self.connection.execute(
            """
            SELECT vendor_id, name, phone, metadata_json
            FROM vendor_targets WHERE vendor_id = ?
            """,
            (vendor_id,),
        ).fetchone()
        if row is None:
            return None
        return VendorTarget(
            vendor_id=row[0],
            name=row[1],
            phone=row[2],
            metadata=json.loads(row[3] or "{}"),
        )


class InMemoryCallerInputGateway:
    def __init__(
        self,
        specs: list[ConfirmedJobSpec] | None = None,
        vendors: list[VendorTarget] | None = None,
    ) -> None:
        self.specs = {item.version_id: item for item in specs or []}
        self.vendors = {item.vendor_id: item for item in vendors or []}

    def get_confirmed_job_spec(self, version_id: str) -> ConfirmedJobSpec | None:
        return self.specs.get(version_id)

    def get_vendor_target(self, vendor_id: str) -> VendorTarget | None:
        return self.vendors.get(vendor_id)


class EstimatorAwareCallerInputGateway:
    """Reads confirmed Estimator specs while leaving vendor ownership upstream."""

    def __init__(self, connection: sqlite3.Connection, upstream) -> None:
        self.specs = SQLiteCallerInputGateway(connection)
        self.upstream = upstream

    def get_confirmed_job_spec(self, version_id: str) -> ConfirmedJobSpec | None:
        return self.specs.get_confirmed_job_spec(
            version_id
        ) or self.upstream.get_confirmed_job_spec(version_id)

    def get_vendor_target(self, vendor_id: str) -> VendorTarget | None:
        return self.upstream.get_vendor_target(vendor_id)
