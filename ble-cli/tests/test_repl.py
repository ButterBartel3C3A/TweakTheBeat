"""REPL argument parsing regressions (the silent-truncation bug).

``write HEX...`` must join EVERY token into one frame — the old code took
only ``parts[1]`` and silently interpreted the next token as listen seconds,
so ``write AA BB CC`` really sent 1 byte and listened for B seconds.
"""

from blecli.repl import parse_sub_args, parse_write_args


def test_write_joins_all_tokens_into_one_frame():
    # 17 tokens must become a 17-byte frame (the bug sent only 1 byte)
    toks = "01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F 10 11".split()
    frame, listen_s, err = parse_write_args(toks)
    assert err is None
    assert listen_s is None
    assert frame == bytes.fromhex("0102030405060708090A0B0C0D0E0F1011")


def test_write_two_tokens_is_not_listen():
    # "DE AD" means the 2-byte frame; AD must not become listen seconds
    frame, listen_s, err = parse_write_args(["DE", "AD"])
    assert err is None and listen_s is None and frame == b"\xde\xad"


def test_write_explicit_listen():
    frame, listen_s, err = parse_write_args(["DE", "AD", "--listen", "3"])
    assert err is None and frame == b"\xde\xad" and listen_s == 3.0


def test_write_listen_flag_anywhere():
    frame, listen_s, err = parse_write_args(["--listen", "1.5", "CA", "FE"])
    assert err is None and frame == b"\xca\xfe" and listen_s == 1.5


def test_write_compact_hex_still_works():
    frame, listen_s, err = parse_write_args(["DEADBEEF"])
    assert err is None and frame == bytes.fromhex("DEADBEEF")


def test_write_errors():
    assert parse_write_args([])[2] is not None
    assert parse_write_args(["--listen"])[2] is not None
    assert parse_write_args(["DE", "--listen", "x"])[2] is not None
    assert parse_write_args(["ZZ"])[2] is not None
    assert parse_write_args(["--listen", "2"])[2] is not None  # no hex


def test_sub_positional_and_listen():
    assert parse_sub_args(["8"]) == (8.0, None)
    assert parse_sub_args(["--listen", "8"]) == (8.0, None)
    assert parse_sub_args(["--timeout", "2.5"]) == (2.5, None)


def test_sub_errors():
    assert parse_sub_args([])[1] is not None
    assert parse_sub_args(["--listen"])[1] is not None
    assert parse_sub_args(["abc"])[1] is not None
