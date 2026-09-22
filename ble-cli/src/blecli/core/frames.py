"""Generic frame matching/decoding machinery (protocol-agnostic).

Frame types are *data*: they come from the profile TOML and from assertion
rule files.  This module only knows the mechanics: prefix/wildcard matching,
byte-slice field extraction, and optional per-type decoder hooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..util import Pattern, fmt_hex, timestamp_ms


@dataclass
class FieldSpec:
    name: str
    byte: int = 0
    length: int = 1
    signed: bool = False


@dataclass
class FrameType:
    name: str
    pattern: Pattern
    fields: list[FieldSpec] = field(default_factory=list)
    hook: str | None = None

    def decode(self, payload: bytes, hooks: dict[str, Callable] | None = None,
               byteorder: str = "big") -> dict[str, Any] | None:
        """Return a decoded record, or None if the payload does not match."""
        if not self.pattern.matches(payload):
            return None
        rec: dict[str, Any] = {"type": self.name, "hex": fmt_hex(payload), "fields": {}}
        for spec in self.fields:
            end = spec.byte + spec.length
            if spec.byte < 0 or end > len(payload):
                rec["fields"][spec.name] = None  # out of range -> explicit null, never crash
                continue
            rec["fields"][spec.name] = int.from_bytes(payload[spec.byte:end], byteorder, signed=spec.signed)
        if self.hook:
            fn = (hooks or {}).get(self.hook)
            if fn is None:
                rec["hook_error"] = f"hook {self.hook!r} not found"
            else:
                try:
                    result = fn(payload)
                    if not isinstance(result, dict):
                        raise TypeError(f"hook {self.hook!r} must return dict, got {type(result).__name__}")
                    rec["fields"].update(result)
                except Exception as exc:  # a broken hook must not lose the raw frame
                    rec["hook_error"] = f"hook {self.hook!r} failed: {exc}"
        return rec


class FrameTable:
    """Ordered list of frame types; first match wins, unmatched -> raw record."""

    def __init__(self, types: list[FrameType], hooks: dict[str, Callable] | None = None,
                 byteorder: str = "big"):
        self.types = types
        self.hooks = hooks or {}
        self.byteorder = byteorder

    def decode(self, payload: bytes) -> dict[str, Any]:
        for ft in self.types:
            rec = ft.decode(payload, self.hooks, self.byteorder)
            if rec is not None:
                return rec
        return {"type": "unknown", "hex": fmt_hex(payload)}

    def record(self, payload: bytes) -> dict[str, Any]:
        rec = self.decode(payload)
        rec["ts"] = timestamp_ms()
        return rec


def make_frame_type(spec: dict[str, Any], default_name: str = "") -> FrameType:
    """Build a FrameType from a TOML dict.

    ``match`` accepts either ``{pattern = "BE EF XX"}`` or
    ``{prefix = [0xBE, 0xEF], len = 3}`` (prefix padded with wildcards).
    """
    match = spec.get("match") or {}
    if "pattern" in match:
        pattern = Pattern(str(match["pattern"]))
    elif "pattern" in spec:
        pattern = Pattern(str(spec["pattern"]))  # shorthand: top-level pattern
    elif "prefix" in match:
        prefix = [int(b) for b in match["prefix"]]
        total = int(match.get("len", len(prefix)))
        if total < len(prefix):
            raise ValueError(f"frame {spec.get('name') or default_name}: len {total} < prefix length {len(prefix)}")
        pattern = Pattern(" ".join(f"{b:02X}" for b in prefix) + " " + " ".join(["XX"] * (total - len(prefix))))
    else:
        raise ValueError(f"frame {spec.get('name') or default_name}: match needs 'pattern' or 'prefix'")
    fields = []
    for f in spec.get("fields") or []:
        if "byte" not in f or "name" not in f:
            raise ValueError(f"frame {spec.get('name') or default_name}: field needs name+byte")
        fields.append(FieldSpec(
            name=str(f["name"]),
            byte=int(f["byte"]),
            length=int(f.get("len", 1)),
            signed=bool(f.get("signed", False)),
        ))
    return FrameType(name=str(spec.get("name") or default_name), pattern=pattern,
                     fields=fields, hook=spec.get("hook"))


def make_frame_table(type_specs: list[dict[str, Any]], byteorder: str = "big",
                     hooks: dict[str, Callable] | None = None) -> FrameTable:
    types = [make_frame_type(s, default_name=f"frame{i}") for i, s in enumerate(type_specs)]
    return FrameTable(types, hooks, byteorder)
