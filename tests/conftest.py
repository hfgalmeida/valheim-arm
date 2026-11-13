import pytest

from app.config import ConfigStore
from app.runtime import Runtime


@pytest.fixture
def config(tmp_path):
    return ConfigStore(tmp_path, {"SERVER_PASSWORD": "game-password", "RESTART_AT": "off", "BACKUP_AT": "off", "AUTO_UPDATE": "false"})


@pytest.fixture
def runtime(tmp_path, config):
    return Runtime(tmp_path, config)
