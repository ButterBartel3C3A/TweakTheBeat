"""Session/handshake tests against a fake backend (no radio needed)."""

import asyncio

import pytest

from blecli.core.connection import open_session
from blecli.errors import BleCliError
from blecli.transport.base import Backend, CharInfo, DeviceInfo, ServiceInfo

SVC = "0000180D-0000-1000-8000-00805F9B34FB"
WRITE = "00002A00-0000-1000-8000-00805F9B34FB"
NOTIFY = "00002A01-0000-1000-8000-00805F9B34FB"


class FakeBackend(Backend):
    """Scripted backend: writes trigger configured notify responses."""

    def __init__(self, responses: dict[bytes, list[bytes]] | None = None):
        self.responses = responses or {}
        self.writes: list[bytes] = []
        self.connected = False
        self.handler = None

    async def scan(self, timeout_s):
        return [DeviceInfo(address="AA:BB:CC:DD:EE:FF", name="DemoDevice-1", rssi=-40)]

    async def connect(self, address, timeout_s):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def get_services(self):
        return [ServiceInfo(uuid=SVC, characteristics=[
            CharInfo(uuid=WRITE, properties=["write-without-response"]),
            CharInfo(uuid=NOTIFY, properties=["notify"]),
        ])]

    async def write(self, uuid, data, with_response):
        self.writes.append(data)
        for resp in self.responses.get(bytes(data), []):
            await asyncio.sleep(0)
            self.handler(resp)

    async def start_notify(self, uuid, handler):
        self.handler = handler

    async def stop_notify(self, uuid):
        self.handler = None

    @property
    def is_connected(self):
        return self.connected

    @property
    def mtu(self):
        return 23


def _profile(tmp_path):
    import tomllib
    from pathlib import Path
    from blecli.profiles.loader import load_profile
    p = tmp_path / "profile.toml"
    p.write_text(f"""
[meta]
name = "fake"

[device]
name_filter = "DemoDevice*"

[[gatt.services]]
service = "{SVC}"

[gatt.chars.write]
uuid = "{WRITE}"
write_type = "write_without_response"

[gatt.chars.notify]
uuid = "{NOTIFY}"

[handshake]
deadline_ms = 500
sequence = [ {{ write = "DE AD" }} ]
expect = [ {{ pattern = "CA FE 01", name = "ack" }} ]
""", encoding="utf-8")
    return load_profile(p)


def test_open_session_handshake_ok(tmp_path):
    backend = FakeBackend(responses={b"\xde\xad": [b"\xca\xfe\x01"]})
    session = asyncio.run(open_session(
        backend, _profile(tmp_path), address="AA:BB:CC:DD:EE:FF"))
    assert session.address == "AA:BB:CC:DD:EE:FF"
    assert len(session.handshake) == 1
    assert session.handshake[0]["hex"] == "CA FE 01"
    assert session.handshake[0]["t_ms"].endswith("Z")  # ISO8601 UTC


def test_open_session_handshake_timeout(tmp_path):
    backend = FakeBackend()  # never answers
    with pytest.raises(BleCliError) as ei:
        asyncio.run(open_session(backend, _profile(tmp_path),
                                 address="AA:BB:CC:DD:EE:FF"))
    assert ei.value.code == "handshake_timeout"


def test_open_session_service_missing(tmp_path):
    backend = FakeBackend()

    async def wrong_services():
        return [ServiceInfo(uuid="0000FFFF-0000-1000-8000-00805F9B34FB",
                            characteristics=[])]

    backend.get_services = wrong_services
    with pytest.raises(BleCliError) as ei:
        asyncio.run(open_session(backend, _profile(tmp_path),
                                 address="AA:BB:CC:DD:EE:FF"))
    assert ei.value.code == "service_not_found"


def test_resolve_address_via_scan(tmp_path):
    from blecli.core.discovery import resolve_address
    backend = FakeBackend()
    addr, name = asyncio.run(resolve_address(
        backend, None, None, ["DemoDevice*"], 0.5))
    assert addr == "AA:BB:CC:DD:EE:FF"
    assert name == "DemoDevice-1"


def test_resolve_address_not_found(tmp_path):
    from blecli.core.discovery import resolve_address
    backend = FakeBackend()

    async def no_devices(timeout_s):
        return []

    backend.scan = no_devices
    with pytest.raises(BleCliError) as ei:
        asyncio.run(resolve_address(backend, None, None, ["NoSuch*"], 0.5,
                                    wake_hint="请唤醒"))
    assert ei.value.code == "device_not_found"
    assert "请唤醒" in ei.value.message
