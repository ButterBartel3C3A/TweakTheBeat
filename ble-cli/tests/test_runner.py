"""Connection-reuse tests (D12): one ``cases run`` shares a single link.

The demo profile + a scripted backend drive the full runner path without
hardware: the handshake is DE AD BE EF -> BE EF XX + CA FE +4B.
"""

import asyncio
from pathlib import Path

from blecli.cases.parser import Case
from blecli.cases.runner import RunOptions, run_cases
from blecli.errors import BleCliError, DISCONNECTED
from blecli.profiles.loader import load_profile
from blecli.transport.base import Backend, CharInfo, DeviceInfo, ServiceInfo

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo_profile"

SVC = "DEADBEEF-1000-4000-8000-000000000001"
WR = "DEADBEEF-1001-4000-8000-000000000001"
NT = "DEADBEEF-1002-4000-8000-000000000001"

RULE_BASIC = """case_id = "{cid}"
mode = "inject"
[[inject]]
write = "DE AD"
[assert]
timeout_ms = 500
[[assert.expect]]
name = "ack"
pattern = "BE EF XX"
"""

# fresh_connection must sit at top level BEFORE any [table] header,
# otherwise TOML scopes it into the preceding table and it is ignored
RULE_FRESH = 'fresh_connection = true\n' + RULE_BASIC


class Scripted(Backend):
    """Handshake DE AD BE EF -> BE EF 01 + CA FE 01 02 03 04; inject DE AD -> BE EF 01."""

    def __init__(self, fail_writes: int = 0):
        self.handler = None
        self.connects = 0
        self.writes: list[bytes] = []
        self.fail_writes = fail_writes

    async def scan(self, timeout_s):
        return [DeviceInfo(address="AA:BB:CC:DD:EE:FF", name="DemoDevice-1", rssi=-40)]

    async def connect(self, address, timeout_s):
        self.connects += 1

    async def disconnect(self):
        pass

    async def get_services(self):
        return [ServiceInfo(uuid=SVC, characteristics=[
            CharInfo(uuid=WR, properties=["write-without-response"]),
            CharInfo(uuid=NT, properties=["notify"])])]

    async def write(self, uuid, data, with_response):
        frame = bytes(data)
        self.writes.append(frame)
        if frame == bytes.fromhex("DE AD") and self.fail_writes > 0:
            self.fail_writes -= 1
            raise BleCliError(DISCONNECTED, "link dropped (scripted)")
        if frame == bytes.fromhex("DE AD BE EF"):
            self.handler(bytes.fromhex("BE EF 01"))
            self.handler(bytes.fromhex("CA FE 01 02 03 04"))
        elif frame == bytes.fromhex("DE AD"):
            self.handler(bytes.fromhex("BE EF 01"))

    async def start_notify(self, uuid, handler):
        self.handler = handler

    async def stop_notify(self, uuid):
        self.handler = None

    @property
    def is_connected(self):
        return True

    @property
    def mtu(self):
        return 23


def _case(cid: str) -> Case:
    return Case(id=cid, group="X", group_title="X 组", inject=None, physical="",
                expect_raw="", note="", source_line=1)


def _run(tmp_path, cids, rule_tmpl, backend_factory):
    rules = tmp_path / "rules"
    rules.mkdir()
    for cid in cids:
        (rules / f"{cid}.toml").write_text(
            rule_tmpl.format(cid=cid), encoding="utf-8")
    profile = load_profile(DEMO / "profile.toml")
    opts = RunOptions(rules_dir=rules, state_path=tmp_path / "state.json",
                      confirm_timeout_s=2, ruleless_window_ms=200)
    return asyncio.run(run_cases([_case(c) for c in cids], profile,
                                 backend_factory, tmp_path / "state.json",
                                 opts, None))


def test_shared_connection_two_cases(tmp_path):
    backends: list[Scripted] = []

    def factory():
        b = Scripted()
        backends.append(b)
        return b

    results = _run(tmp_path, ["X1.1", "X2.1"], RULE_BASIC, factory)
    assert [r.result for r in results] == ["PASS", "PASS"]
    assert len(backends) == 1            # ONE connection for the whole run
    assert backends[0].connects == 1
    # handshake frame once + the two inject frames
    assert backends[0].writes.count(bytes.fromhex("DE AD BE EF")) == 1
    assert backends[0].writes.count(bytes.fromhex("DE AD")) == 2
    # second case's window must not leak the first case's uplinks
    assert len(results[1].uplinks) == 1


RULE_OBSERVE_FRESH = """fresh_connection = true
case_id = "{cid}"
mode = "observe"
[assert]
timeout_ms = 500
[[assert.expect]]
name = "sn"
pattern = "CA FE +4B"
"""


def test_observe_fresh_sees_handshake_uplinks(tmp_path):
    # C1.2 semantics: an observe case asserting the HANDSHAKE frames must
    # declare fresh_connection; the reopened session's handshake uplinks
    # belong to its window (not cleared)
    backends: list[Scripted] = []

    def factory():
        b = Scripted()
        backends.append(b)
        return b

    results = _run(tmp_path, ["X1.1"], RULE_OBSERVE_FRESH, factory)
    assert results[0].result == "PASS"
    # window contains the handshake frames (sn 53-style, demo: CA FE +4B)
    assert any(u["type"] == "sn" for u in results[0].uplinks)
    assert len(backends) == 1


def test_fresh_connection_reopens(tmp_path):
    backends: list[Scripted] = []

    def factory():
        b = Scripted()
        backends.append(b)
        return b

    results = _run(tmp_path, ["X1.1", "X2.1"], RULE_FRESH, factory)
    assert [r.result for r in results] == ["PASS", "PASS"]
    assert len(backends) == 2            # fresh_connection forced a reopen
    assert [b.connects for b in backends] == [1, 1]


def test_link_drop_retries_case_once(tmp_path):
    backends: list[Scripted] = []

    def factory():
        # only the FIRST backend drops once; the reconnect must be healthy
        b = Scripted(fail_writes=1 if not backends else 0)
        backends.append(b)
        return b

    results = _run(tmp_path, ["X1.1", "X2.1"], RULE_BASIC, factory)
    assert [r.result for r in results] == ["PASS", "PASS"]
    assert len(backends) == 2            # reconnect after the drop
    # the dropped case retried its inject after reconnecting
    assert sum(b.writes.count(bytes.fromhex("DE AD")) for b in backends) == 3
