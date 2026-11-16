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


def test_legacy_directory_migrates_and_crossplay_steam_identity_is_explicit(tmp_path):
    path = tmp_path / "known.json"
    path.write_text(json.dumps({"Steam_76561198000000001": 1000}))
    directory = PlayerDirectory(path)
    assert directory.describe()[0]["confirmed"]
    directory.consume(
        "11/16/2026 10:00:00: PlayFab socket with remote ID playfab/ABC123 received local Platform ID Steam_76561198000000002"
    )
    records = {item["id"]: item for item in directory.describe()}
    assert len(records) == 2
    assert records["Steam_76561198000000002"]["confirmed"]
    assert PlayerDirectory(path).describe() == directory.describe()


def test_other_platform_is_candidate_until_admin_confirms_and_names_it(tmp_path):
    path = tmp_path / "known.json"
    directory = PlayerDirectory(path)
    event = "PlayFab socket with remote ID playfab/ABC123 received local Platform ID Xbox_12345"
    directory.consume(event)
    observed = directory.describe()[0]
    assert observed["platform"] == "Xbox"
    assert not observed["confirmed"] and observed["name"] == ""
    seen = observed["last_seen"]
    result = directory.register("Xbox_12345", "Jogador Xbox")
    assert result["confirmed"] and result["source"] == "admin"
    assert result["last_seen"] == seen
    directory.consume(event)
    result = PlayerDirectory(path).describe()[0]
    assert result["name"] == "Jogador Xbox" and result["confirmed"]
    assert result["source"] == "admin"


def test_names_are_never_associated_by_adjacent_events_and_manual_id_is_unchanged(tmp_path):
    directory = PlayerDirectory(tmp_path / "known.json")
    directory.consume("Got connection SteamID 76561198000000001")
    directory.consume("Got character ZDOID from Wrong Player : 123:1")
    assert directory.describe()[0]["name"] == ""
    result = directory.register("X_987654321", "Nome informado", "Xbox")
    assert result["id"] == "X_987654321"
    assert result["last_seen"] is None
    assert result["platform"] == "Xbox"
    restored = PlayerDirectory(tmp_path / "known.json")
    assert (
        next(item for item in restored.describe() if item["id"] == result["id"])["platform"]
        == "Xbox"
    )


@pytest.mark.parametrize(
    "line",
    [
        "Error: PlayFab socket with remote ID playfab/ABC received local Platform ID Xbox_123",
        "Got character ZDOID from PlayFab socket with remote ID playfab/ABC received local Platform ID Xbox_123",
        "PlayFab socket with remote ID playfab/ABC received local Platform ID PlayFab_ABC",
        "PlayFab socket with remote ID playfab/ABC received local Platform ID Xbox_123<script>",
    ],
)
def test_crossplay_handles_and_embedded_text_never_become_candidates(tmp_path, line):
    directory = PlayerDirectory(tmp_path / "known.json")
    directory.consume(line)
    assert directory.describe() == []


def test_registration_disk_failure_preserves_in_memory_directory(tmp_path):
    directory = PlayerDirectory(tmp_path / "known.json")
    directory.register("Xbox_12345", "Original")
    with patch("app.player_directory.atomic_json", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            directory.register("Xbox_12345", "Changed")
    assert directory.describe()[0]["name"] == "Original"
