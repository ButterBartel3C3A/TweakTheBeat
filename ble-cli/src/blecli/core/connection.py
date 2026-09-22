"""Connection session lifecycle (design.md: every AI command re-connects and
replays the profile handshake, because BLE links cannot cross processes).

``open_session`` = resolve address -> connect -> verify GATT -> subscribe
notify -> run the profile handshake sequence until its expectations are met
(or the deadline -> ``handshake_timeout``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..errors import BleCliError, HANDSHAKE_TIMEOUT
from ..transport.base import Backend, ServiceInfo
from .discovery import resolve_address
from .gatt import verify_services
from .notify import UplinkRecorder

if TYPE_CHECKING:
    from ..profiles.loader import Profile


@dataclass
class Session:
    backend: Backend
    profile: "Profile"
    recorder: UplinkRecorder
    address: str
    device_name: str | None
    services: list[ServiceInfo] = field(default_factory=list)
    handshake: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mtu(self) -> int:
        return self.backend.mtu


async def open_session(
    backend: Backend,
    profile: "Profile",
    *,
    address: str | None = None,
    state_address: str | None = None,
    run_handshake: bool = True,
) -> Session:
    address, name = await resolve_address(
        backend, address, state_address,
        profile.device_name_filters, profile.scan_timeout_s, profile.device_wake_hint,
    )
    await backend.connect(address, profile.connect_timeout_s)

    services = await backend.get_services()
    verify_services(services, profile)

    recorder = UplinkRecorder(profile.frame_table)
    if profile.notify_char_uuid:
        await backend.start_notify(profile.notify_char_uuid, recorder.on_notify)

    session = Session(backend=backend, profile=profile, recorder=recorder,
                      address=address, device_name=name, services=services)

    if run_handshake and profile.handshake is not None:
        session.handshake = await _run_handshake(session)
    return session


async def _run_handshake(session: Session) -> list[dict[str, Any]]:
    hs = session.profile.handshake
    session.recorder.clear()
    try:
        for step in hs.sequence:
            await session.backend.write(session.profile.write_char_uuid, step, session.profile.write_with_response)
    except BleCliError:
        raise
    except Exception as exc:
        raise BleCliError("write_failed", f"handshake write failed: {exc}") from exc

    deadline = time.monotonic() + hs.deadline_ms / 1000.0

    def all_matched(uplinks: list[dict]) -> bool:
        return all(any(e.pattern.matches(bytes.fromhex(u["hex"].replace(" ", ""))) for u in uplinks) for e in hs.expect)

    while time.monotonic() < deadline:
        if all_matched(session.recorder.uplinks):
            break
        await asyncio.sleep(0.05)

    log = session.recorder.snapshot()
    missing = [e.pattern.text for e in hs.expect
               if not any(e.pattern.matches(bytes.fromhex(u["hex"].replace(" ", ""))) for u in log)]
    if missing:
        raise BleCliError(
            HANDSHAKE_TIMEOUT,
            f"handshake incomplete after {hs.deadline_ms}ms; missing: {missing}; "
            f"uplinks: {[u['hex'] for u in log] or 'none'}",
        )
    return log


async def close_session(session: Session) -> None:
    if session.profile.notify_char_uuid:
        await session.backend.stop_notify(session.profile.notify_char_uuid)
    await session.backend.disconnect()
