import pytest

from app.config import ConfigStore, Settings


def test_precedence_persistence_reset(tmp_path, config):
    assert config.describe()["sources"]["server_name"] == "padrão"
    config.change({"server_name": "Painel"}, [])
    restored = ConfigStore(tmp_path, {"SERVER_PASSWORD": "game-password", "SERVER_NAME": "Compose"})
    assert restored.effective().server_name == "Painel"
    assert restored.describe()["sources"]["server_name"] == "painel"
    restored.change({}, ["server_name"])
    assert restored.effective().server_name == "Compose"
    assert restored.describe()["values"]["server_password"] == ""


def test_world_confirmation_and_validation(config):
    with pytest.raises(ValueError, match="Confirme"):
        config.change({"world_name": "Novo"}, [])
    config.change({"world_name": "Novo"}, [], True)
    with pytest.raises(ValueError):
        config.change({"world_name": "../escape"}, [], True)
    with pytest.raises(ValueError):
        config.change({"modifier_combat": "normal"}, [])
    with pytest.raises(ValueError):
        config.change({"unknown": True}, [])
    with pytest.raises(ValueError):
        config.change({"server_port": 3000}, [])
    assert config.effective().world_name == "Novo"


def test_arguments_no_shell_injection_and_order(tmp_path):
    settings = Settings(server_password="secret-password", server_name='Name; $(whoami)', preset="Hard",
                        modifier_combat="easy", modifier_resources="most", key_nomap=True, crossplay=True)
    args = settings.arguments(tmp_path)
    assert args[args.index("-name") + 1] == 'Name; $(whoami)'
    assert args.index("-preset") < args.index("-modifier")
    assert ["-modifier", "resources", "most"] == args[args.index("resources")-1:args.index("resources")+2]
    assert "-crossplay" in args and "nomap" in args


@pytest.mark.parametrize("field,value", [("restart_at", "25:00"), ("backup_at", "5:00"), ("timezone", "Mars/Base"),
    ("save_interval", 0), ("server_password", "1234"), ("bepinex", True)])
def test_invalid_settings(config, field, value):
    with pytest.raises(ValueError):
        config.change({field: value}, [])
