import json
from unittest.mock import patch

import pytest

from app.player_directory import PlayerDirectory


def test_explicit_steam_identity_persists_without_becoming_online_count(tmp_path):
    path = tmp_path / "known.json"
    directory = PlayerDirectory(path)
    directory.consume("11/13/2026 19:22:21: Got connection SteamID 76561198000000001")
    assert directory.describe()[0]["id"] == "Steam_76561198000000001"
    assert PlayerDirectory(path).describe() == directory.describe()
    assert "count" not in directory.describe()[0]


@pytest.mark.parametrize(
    "line",
    [
        "Got character ZDOID from Player : 1779906771:1",
        "Keep socket for playfab/D5F2C6788C2EB785, try to reconnect before timeout",
        "Session world with join code 123456",
        "Got connection SteamID 123",
        "Got character ZDOID from Bad: Got connection SteamID 76561198000000001",
        "Got connection SteamID 76561198000000001<script>",
    ],
)
def test_ambiguous_handles_and_character_names_are_not_permission_ids(tmp_path, line):
    directory = PlayerDirectory(tmp_path / "known.json")
    directory.consume(line)
    assert directory.describe() == []


def test_discovery_disk_failure_does_not_interrupt_log_reader(runtime):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    runtime.process = SimpleNamespace(
        returncode=0,
        stdout=SimpleNamespace(
            readline=AsyncMock(
                side_effect=[
                    b"Got connection SteamID 76561198000000001\n",
                    b"World save (5/5) done.\n",
                    b"",
                ]
            )
        ),
        wait=AsyncMock(return_value=0),
    )
    with patch("app.player_directory.atomic_json", side_effect=OSError("disk full")):
        asyncio.run(runtime.read_game(runtime.process))
    assert runtime.last_save > 0
    assert runtime.player_directory.describe()[0]["id"] == "Steam_76561198000000001"


def test_directory_is_bounded_and_bad_metadata_does_not_prevent_boot(tmp_path):
    path = tmp_path / "known.json"
    path.write_text("not-json")
    directory = PlayerDirectory(path)
    with (
        patch("app.player_directory.MAX_PLAYERS", 2),
        patch("app.player_directory.time.time", side_effect=[1, 2, 3]),
    ):
        for player_id in ("76561198000000001", "76561198000000002", "76561198000000003"):
            directory.consume("Got connection SteamID " + player_id)
    assert len(directory.describe()) == 2
    assert "Steam_76561198000000001" not in json.loads(path.read_text())
