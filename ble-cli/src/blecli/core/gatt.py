"""GATT verification against a profile (service/characteristic presence)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import BleCliError, CHAR_NOT_FOUND, SERVICE_NOT_FOUND
from ..transport.base import ServiceInfo

if TYPE_CHECKING:  # duck-typed at runtime to keep core independent of the loader
    from ..profiles.loader import Profile


def verify_services(services: list[ServiceInfo], profile: "Profile") -> dict[str, bool]:
    """Check that every service UUID the profile needs exists and exposes the
    required characteristic UUIDs.  Raises on the first missing item."""
    by_uuid = {s.uuid: s for s in services}

    for svc_uuid in profile.service_uuids:
        svc = by_uuid.get(svc_uuid)
        if svc is None:
            raise BleCliError(SERVICE_NOT_FOUND, f"service {svc_uuid} not found in GATT table")
        for ch_uuid in profile.char_uuids:
            if not any(c.uuid == ch_uuid for c in svc.characteristics):
                raise BleCliError(CHAR_NOT_FOUND, f"characteristic {ch_uuid} not found in service {svc_uuid}")

    found = {}
    for ch_uuid in profile.char_uuids:
        found[ch_uuid] = any(ch_uuid in [c.uuid for c in s.characteristics] for s in services)
    return found


def gatt_tree(services: list[ServiceInfo]) -> dict:
    return {"services": [s.to_dict() for s in services]}
