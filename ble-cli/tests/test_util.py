import pytest

from blecli.util import Pattern, fmt_hex, parse_hex


def test_parse_hex_basic():
    assert parse_hex("AA BB CC") == b"\xaa\xbb\xcc"
    assert parse_hex("aabbcc") == b"\xaa\xbb\xcc"
    assert parse_hex("0xAA BB") == b"\xaa\xbb"
    assert parse_hex("AA:BB-CC_DE") == b"\xaa\xbb\xcc\xde"


def test_parse_hex_errors():
    with pytest.raises(ValueError):
        parse_hex("AA ZZ")
    with pytest.raises(ValueError):
        parse_hex("A")


def test_fmt_hex():
    assert fmt_hex(b"\xaa\x0b") == "AA 0B"


def test_pattern_exact():
    p = Pattern("BE EF 01")
    assert p.matches(b"\xbe\xef\x01")
    assert not p.matches(b"\xbe\xef\x02")
    assert not p.matches(b"\xbe\xef")


def test_pattern_wildcard():
    p = Pattern("BE EF XX")
    assert p.matches(b"\xbe\xef\x01")
    assert p.matches(b"\xbe\xef\xff")
    assert not p.matches(b"\xbe\xee\x01")
    assert not p.matches(b"\xbe\xef\x01\x00")


def test_pattern_suffix_len():
    p = Pattern("CA FE +4B")
    assert p.matches(bytes.fromhex("CA FE 00 00 00 01"))
    assert not p.matches(bytes.fromhex("CA FE 00"))
    assert not p.matches(bytes.fromhex("CA FE 00 00 00 01 02"))
