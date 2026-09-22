"""Default backend on top of bleak (Windows/WinRT, macOS/CoreBluetooth, Linux/BlueZ)."""

from __future__ import annotations

import asyncio
from typing import Callable

from bleak import BleakClient, BleakError, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

from ..errors import BleCliError, BLE_OS_ERROR, CONNECT_FAILED, NOTIFY_FAILED, WRITE_FAILED, die
from .base import Backend, CharInfo, DeviceInfo, ServiceInfo


def _map_exc(exc: Exception, code: str, what: str) -> BleCliError:
    return BleCliError(code, f"{what}: {exc}")


class BleakBackend(Backend):
    def __init__(self, write_type: str = "write_without_response"):
        self._write_type = write_type
        self._client: BleakClient | None = None

    # -- scanning ---------------------------------------------------------

    async def scan(self, timeout_s: float) -> list[DeviceInfo]:
        devices: dict[str, DeviceInfo] = {}
        try:
            async with BleakScanner() as scanner:
                await asyncio.sleep(timeout_s)
                for d in scanner.discovered_devices:
                    info = DeviceInfo(address=d.address, name=d.name, rssi=d.rssi)
                    prev = devices.get(d.address)
                    # keep the strongest sighting
                    if prev is None or (info.rssi is not None and (prev.rssi is None or info.rssi < prev.rssi)):
                        devices[d.address] = info
        except BleakError as exc:
            raise _map_exc(exc, BLE_OS_ERROR, "scan failed") from exc
        return sorted(devices.values(), key=lambda d: d.rssi if d.rssi is not None else 127)

    # -- connection --------------------------------------------------------

    async def connect(self, address: str, timeout_s: float) -> None:
        self._client = BleakClient(address, timeout=timeout_s)
        try:
            await self._client.connect()
        except (BleakError, asyncio.TimeoutError, OSError) as exc:
            self._client = None
            raise _map_exc(exc, CONNECT_FAILED, f"connect to {address}") from exc

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def mtu(self) -> int:
        return int(self._client.mtu_size) if self._client and self._client.mtu_size is not None else 0

    # -- GATT ---------------------------------------------------------------

    async def get_services(self) -> list[ServiceInfo]:
        if self._client is None:
            raise die("disconnected", "not connected")
        try:
            services = self._client.services
        except (BleakError, OSError) as exc:
            raise _map_exc(exc, BLE_OS_ERROR, "GATT discovery") from exc
        out: list[ServiceInfo] = []
        for svc in services.services:
            info = ServiceInfo(uuid=svc.uuid)
            for ch in svc.characteristics:
                info.characteristics.append(CharInfo(uuid=ch.uuid, properties=sorted(ch.properties)))
            out.append(info)
        return out

    async def write(self, uuid: str, data: bytes, with_response: bool) -> None:
        if self._client is None:
            raise die("disconnected", "not connected")
        try:
            await self._client.write_gatt_char(uuid, data, response=with_response)
        except (BleakError, OSError) as exc:
            raise _map_exc(exc, WRITE_FAILED, f"write {uuid}") from exc

    async def start_notify(self, uuid: str, handler: Callable[[bytes], None]) -> None:
        if self._client is None:
            raise die("disconnected", "not connected")

        def _cb(ch: BleakGATTCharacteristic, data: bytearray) -> None:
            handler(bytes(data))

        try:
            await self._client.start_notify(uuid, _cb)
        except (BleakError, OSError) as exc:
            raise _map_exc(exc, NOTIFY_FAILED, f"subscribe {uuid}") from exc

    async def stop_notify(self, uuid: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.stop_notify(uuid)
        except Exception:
            pass
