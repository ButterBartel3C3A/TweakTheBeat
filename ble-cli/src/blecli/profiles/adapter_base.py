"""Optional base class for a profile's adapter.py hooks (D2 escape hatch).

A profile may declare ``[hooks] module = "adapter"`` and provide
``adapter.py`` next to its ``profile.toml``.  Decoder hooks have the fixed
signature ``decode_*(payload: bytes) -> dict``; this base class just offers
convenience readers so adapter modules stay short.
"""

from __future__ import annotations

import struct


class AdapterBase:
    @staticmethod
    def u8(payload: bytes, off: int = 0) -> int:
        return payload[off]

    @staticmethod
    def u16(payload: bytes, off: int = 0, byteorder: str = "big") -> int:
        return int.from_bytes(payload[off:off + 2], byteorder)

    @staticmethod
    def u32(payload: bytes, off: int = 0, byteorder: str = "big") -> int:
        return int.from_bytes(payload[off:off + 4], byteorder)

    @staticmethod
    def i16(payload: bytes, off: int = 0, byteorder: str = "big") -> int:
        return int.from_bytes(payload[off:off + 2], byteorder, signed=True)

    @staticmethod
    def i32(payload: bytes, off: int = 0, byteorder: str = "big") -> int:
        return int.from_bytes(payload[off:off + 4], byteorder, signed=True)

    @staticmethod
    def f32(payload: bytes, off: int = 0, byteorder: str = "big") -> float:
        fmt = ">f" if byteorder == "big" else "<f"
        return struct.unpack(fmt, payload[off:off + 4])[0]

    @staticmethod
    def scaled(payload: bytes, off: int, length: int, divisor: float,
               signed: bool = False, byteorder: str = "big") -> float:
        raw = int.from_bytes(payload[off:off + length], byteorder, signed=signed)
        return raw / divisor
