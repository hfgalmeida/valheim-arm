import socket

import pytest
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


def test_ten_minute_snapshots_require_new_empty_window_after_departure():
    now = [0.0]
    players = Players(lambda: now[0])
    players.consume("Connections 0 ZDOS:123 sent:0 recv:0")
    now[0] = 120
    assert players.idle()
    players.consume("Got handshake from client ANON")
    assert players.current() is None and not players.idle()
    now[0] = 600
    players.consume("Connections 1 ZDOS:123 sent:0 recv:0")
    assert players.current() == 1 and not players.idle()
    now[0] = 650
    players.consume("ZRpc timeout detected")
    assert players.current() is None and not players.idle()
    now[0] = 1200
    players.consume("Connections 0 ZDOS:123 sent:0 recv:0")
    assert players.status()["idle_wait_seconds"] == 120
    now[0] = 1319
    assert not players.idle()
    now[0] = 1320
    assert players.idle()
    now[0] = 1800
    players.consume("Connections 0 ZDOS:123 sent:0 recv:0")
    assert players.idle()
    now[0] = 2460
    assert players.current() is None and not players.idle()


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


@pytest.mark.parametrize(
    "event",
    [
        '11/14/2026 01:00:11: Player connection lost server "Test" that has join code 123456, now 1 player(s)',
        "11/14/2026 01:01:40: ZRpc timeout detected",
        "ZPlayFabSocket::Dispose. State: CONNECTED",
        "ZPlayFabSocket::Dispose. State: CLOSED",
        "Keep socket for playfab/ANON, try to reconnect before timeout",
        "Failed to send, suspend TX on playfab/ANON while trying to reconnect",
        "PlayFab network error in session 'Test' with code '4098'",
        "ResetParty",
        "Task: LeaveNetworkTask",
        "Task: JoinPartyTask",
        "Joined PlayFab Party network with ID ANON",
        "Got status changed msg k_ESteamNetworkingConnectionState_ProblemDetectedLocally",
        "Got status changed msg k_ESteamNetworkingConnectionState_ClosedByPeer",
        "Game server disconnected",
        "Got handshake from client ANON",
        'Session "Test" with join code 123456 and IP 192.0.2.1:2456 is active with 0 player(s)',
    ],
)
def test_real_connection_events_never_infer_zero_or_keep_old_empty_window(event):
    now = [1000.0]
    players = Players(lambda: now[0])
    players.consume("Connections 0 ZDOS:123")
    now[0] += 120
    assert players.idle()
    players.consume(event)
    status = players.status()
    assert status["count"] is None
    assert status["last_count"] == 0
    assert status["state"] == "transition"
    assert not players.idle()
    assert status["empty_for_seconds"] is None
    players.consume("Connections 0 ZDOS:123")
    assert not players.idle()


def test_expired_zero_does_not_reuse_old_idle_period_without_status_polling():
    now = [1000.0]
    players = Players(lambda: now[0])
    players.consume("Connections 0 ZDOS:1")
    now[0] += 661
    players.consume("Connections 0 ZDOS:1")
    assert not players.idle()
    assert players.status()["idle_wait_seconds"] == 120
    now[0] += 120
    assert players.idle()


def test_snapshot_metadata_age_and_expiration_preserve_last_count():
    now, wall = [1000.0], [2000.0]
    players = Players(lambda: now[0], lambda: wall[0])
    assert players.status()["state"] == "unknown"
    assert players.status()["updated_at"] is None
    players.consume("11/16/2026 12:00:00:  Connections 2 ZDOS:13884  sent:0 recv:0")
    now[0] += 10
    status = players.status()
    assert status["count"] == 2 and status["last_count"] == 2
    assert status["source"] == "game_log" and status["updated_at"] == 2000
    assert status["age_seconds"] == 10 and status["expires_in_seconds"] == 650
    now[0] += 650
    assert players.status()["state"] == "stale"
    assert players.status()["count"] is None
    assert players.status()["last_count"] == 2


@pytest.mark.parametrize(
    "line",
    [
        "Connections -1 ZDOS:3",
        "Connections 0 ZDOS:invalid",
        "Connections 0 ZDOS:2 garbage",
        "Connections 0 ZDOS:2 sent:bad recv:0",
    ],
)
def test_malformed_snapshot_invalidates_previous_empty_state(line):
    now = [1000.0]
    players = Players(lambda: now[0])
    players.consume("Connections 0 ZDOS:1")
    now[0] += 120
    assert players.idle()
    players.consume(line)
    assert players.current() is None
    assert not players.idle()


def test_player_names_and_errors_cannot_forge_zero_snapshot():
    players = Players()
    players.consume("Connections 2 ZDOS:1")
    players.consume("Player history: Connections 0 ZDOS:1")
    players.consume("Error: Connections 0 ZDOS:1")
    assert players.current() == 2
    players.consume("Got character ZDOID from Connections 0 ZDOS:1")
    assert players.current() is None


def test_repeated_zero_snapshot_does_not_restart_continuous_idle_window():
    now = [1000.0]
    players = Players(lambda: now[0])
    players.consume("Connections 0 ZDOS:1")
    now[0] += 60
    players.consume("Connections 0 ZDOS:1")
    now[0] += 60
    assert players.idle()
    assert players.status()["empty_for_seconds"] == 120


def test_steam_divergence_invalidates_and_never_confirms_empty_from_query_alone():
    now = [1000.0]
    players = Players(lambda: now[0], lambda: 2000)
    players.observe_steam(0)
    assert players.current() is None and not players.idle()
    players.consume("Connections 0 ZDOS:1")
    now[0] += 120
    players.observe_steam(1)
    assert players.current() is None and not players.idle()
    assert players.status()["steam"]["count"] == 1
    assert "disagrees" in players.status()["reason"]
    players.observe_steam(None)
    assert players.status()["steam"]["count"] is None
    assert players.current() is None
    players.reset()
    assert players.status()["last_count"] is None
    assert players.status()["steam"]["updated_at"] is None
