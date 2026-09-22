"""Device discovery: scan + name filtering + address resolution."""

from __future__ import annotations

from ..errors import BleCliError, DEVICE_NOT_FOUND, die
from ..util import name_matches
from ..transport.base import Backend, DeviceInfo


async def scan(backend: Backend, timeout_s: float, name_filters: list[str] | None = None) -> list[DeviceInfo]:
    """Scan and optionally filter by glob name patterns."""
    devices = await backend.scan(timeout_s)
    if not name_filters:
        return devices
    out = [d for d in devices if any(name_matches(d.name, pat) for pat in name_filters)]
    return out


async def resolve_address(
    backend: Backend,
    explicit: str | None,
    state_address: str | None,
    name_filters: list[str] | None,
    scan_timeout_s: float,
    wake_hint: str | None = None,
) -> tuple[str, str | None]:
    """Pick the device address: explicit arg > state file > scan by name filter.

    Returns (address, name).  Raises device_not_found when a scan finds no
    matching device.
    """
    if explicit:
        return explicit, None
    if state_address:
        return state_address, None
    devices = await scan(backend, scan_timeout_s, name_filters)
    if not devices:
        hint = f" ({wake_hint})" if wake_hint else ""
        raise BleCliError(DEVICE_NOT_FOUND, f"no device matching name filter {name_filters!r} found in {scan_timeout_s:g}s{hint}")
    best = devices[0]
    return best.address, best.name
