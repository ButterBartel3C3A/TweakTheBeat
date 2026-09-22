"""Shared small helpers: hex parsing/formatting, pattern matching, timestamps.

Hex convention (see design.md section 4): payloads are always rendered as
uppercase space-separated pairs, e.g. ``"AA BB CC"``.  Parsing is lenient on
input: spaces, ``:``, ``-``, ``_``, commas and ``0x`` prefixes are all
accepted separators.
"""

from __future__ import annotations

import fnmatch
import re
import time
from datetime import datetime, timezone

HEX_RE = re.compile(r"^[0-9A-Fa-f]{2}$")
SEP_RE = re.compile(r"[\s:,_-]+")


def parse_hex(text: str) -> bytes:
    """Parse a hex string into bytes. Lenient input: spaces, ``:``, ``-``,
    ``_``, commas and ``0x`` prefixes are accepted; pairs may be run
    together (``aabbcc`` == ``AA BB CC``)."""
    compact = SEP_RE.sub("", text.strip().replace("0x", "").replace("0X", ""))
    if not compact:
        return b""
    if len(compact) % 2 != 0:
        raise ValueError(f"odd number of hex digits in {text!r}")
    if not re.fullmatch(r"[0-9A-Fa-f]+", compact):
        bad = re.sub(r"[0-9A-Fa-f]", "", compact)
        raise ValueError(f"invalid hex characters {bad!r} in {text!r}")
    return bytes.fromhex(compact)


def fmt_hex(data: bytes | bytearray | memoryview) -> str:
    return " ".join(f"{b:02X}" for b in data)


def timestamp_ms() -> str:
    """ISO8601 UTC with milliseconds, e.g. 2026-09-22T07:12:03.421Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}Z"


class Pattern:
    """A frame pattern like ``"BE EF XX"`` (byte wildcards) or ``"CA FE +4B"``
    (suffix length).  Used by handshake expectations and assertion rules alike.

    Semantics: all non-``XX`` bytes must match the payload exactly at the
    given position; with a ``+4B`` suffix the payload must have exactly
    ``len(bytes) + 4`` bytes; otherwise the payload length must equal the
    pattern length.
    """

    def __init__(self, text: str):
        self.text = text.strip()
        m = re.fullmatch(r"(.*)\s*\+\s*(\d+)\s*B", self.text, re.IGNORECASE)
        if m:
            self.text = m.group(1).strip()
            self.extra_len = int(m.group(2))
        else:
            self.extra_len = 0
        self._bytes: list[int | None] = []
        for tok in SEP_RE.split(self.text):
            if not tok:
                continue
            if tok.upper() == "XX":
                self._bytes.append(None)
            elif HEX_RE.match(tok):
                self._bytes.append(int(tok, 16))
            else:
                raise ValueError(f"invalid pattern token {tok!r} in {text!r}")

    @property
    def exact_len(self) -> int:
        return len(self._bytes) + self.extra_len

    def matches(self, payload: bytes | bytearray) -> bool:
        if len(payload) != self.exact_len:
            return False
        for i, want in enumerate(self._bytes):
            if want is not None and payload[i] != want:
                return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Pattern({self.text!r}+{self.extra_len}B)" if self.extra_len else f"Pattern({self.text!r})"


def compile_pattern(text: str) -> Pattern:
    try:
        return Pattern(text)
    except ValueError as exc:
        raise ValueError(str(exc)) from None


def name_matches(name: str | None, pattern: str | None) -> bool:
    if not pattern or not name:
        return False
    return fnmatch.fnmatch(name, pattern)
