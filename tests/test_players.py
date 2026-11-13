import socket
from unittest.mock import patch

from app.players import Players, query_steam


def test_unknown_empty_then_join_expiry():
    now = [1000.0]
    players = Players(lambda: now[0])
    assert not players.idle()
    players.consume("11/13/2026: Connections 0 ZDOS:123 sent:0 recv:0")
    now[0] += 119
    assert not players.idle()
    now[0] += 1
    assert players.idle()
    players.consume("Got connection SteamID 123")
    assert players.current() is None and not players.idle()
    players.consume("Connections 1 ZDOS:123")
    players.consume("Closing socket 123")
    assert players.current() is None
    players.consume("Connections 0 ZDOS:123")
    now[0] += 661
    assert players.current() is None


def test_crossplay_and_restart_never_infer_zero():
    players = Players()
    players.consume("Connections 0 ZDOS:1")
    players.consume("PlayFab socket connected Steam_123")
    assert players.current() is None
    players.consume("Connections 3 ZDOS:1")
    assert players.current() == 3
    players.reset()
    assert players.current() is None


def test_a2s_challenge_and_malformed():
    info = b"\xff\xff\xff\xffI\x11Name\0Map\0Folder\0Game\0\x00\x00\x03"
    with patch("app.players.socket.socket") as cls:
        client = cls.return_value.__enter__.return_value
        client.recv.side_effect = [b"\xff\xff\xff\xffA1234", info]
        assert query_steam(2457) == 3
        assert client.sendto.call_count == 2
        client.recv.side_effect = [b"bad"]
        assert query_steam(2457) is None
        client.recv.side_effect = socket.timeout()
        assert query_steam(2457) is None
