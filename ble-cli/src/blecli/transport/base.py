"""Backend abstraction (design.md section 1). A future bumble/HCI backend
implements this interface; the core layer only ever talks to ``Backend``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

NotifyHandler = Callable[[bytes], None]


@dataclass
class DeviceInfo:
    address: str
    name: str | None
    rssi: int | None = None

    def to_dict(self) -> dict:
        return {"address": self.address, "name": self.name, "rssi": self.rssi}


@dataclass
class CharInfo:
    uuid: str
    properties: list[str]

    def to_dict(self) -> dict:
        return {"uuid": self.uuid, "properties": self.properties}


@dataclass
class ServiceInfo:
    uuid: str
    characteristics: list[CharInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"uuid": self.uuid, "characteristics": [c.to_dict() for c in self.characteristics]}


class Backend(ABC):
    """One BLE connection lifecycle. Instances are not reusable after
    ``disconnect`` — create one per command (matches the AI-mode model)."""

    @abstractmethod
    async def scan(self, timeout_s: float) -> list[DeviceInfo]:
        """Active scan, deduplicated by address."""

    @abstractmethod
    async def connect(self, address: str, timeout_s: float) -> None:
        """Connect. Raises BleCliError(connect_failed) on failure."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Best-effort disconnect; never raises."""

    @abstractmethod
    async def get_services(self) -> list[ServiceInfo]:
        """Discover the full GATT table (requires connection)."""

    @abstractmethod
    async def write(self, uuid: str, data: bytes, with_response: bool) -> None:
        """Write to a characteristic. Raises BleCliError(write_failed)."""

    @abstractmethod
    async def start_notify(self, uuid: str, handler: NotifyHandler) -> None:
        """Subscribe (write CCCD). Raises BleCliError(notify_failed)."""

    @abstractmethod
    async def stop_notify(self, uuid: str) -> None:
        """Unsubscribe, best-effort."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the underlying link is up."""

    @property
    @abstractmethod
    def mtu(self) -> int:
        """Negotiated MTU (0 if unknown)."""
