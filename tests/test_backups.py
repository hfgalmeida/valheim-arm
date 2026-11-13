import io
import json
import shutil
import tarfile
from unittest.mock import patch

import pytest

from app.backups import Backups


def save_fixture(tmp_path):
    saves = tmp_path / "saves/worlds_local"
    saves.mkdir(parents=True)
    (saves / "Dedicated.db").write_bytes(b"world-data")
    (saves / "Dedicated.fwl").write_bytes(b"metadata")
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config/admin.json").write_text('{"hash":"never-export"}')
    return saves


def test_roundtrip_and_admin_excluded(tmp_path, config):
    saves = save_fixture(tmp_path)
    backups = Backups(tmp_path)
    name = backups.create("manual", config.effective(), "123")
    manifest, _ = backups.validate(backups.resolve(name))
    assert "config/admin.json" not in manifest["files"]
    (saves / "Dedicated.db").write_bytes(b"changed")
    (saves / "unexpected.db").write_bytes(b"remove-on-restore")
    backups.restore(name)
    assert (saves / "Dedicated.db").read_bytes() == b"world-data"
    assert not (saves / "unexpected.db").exists()
    assert json.loads((tmp_path / "config/admin.json").read_text())["hash"] == "never-export"


def test_disk_failure_preserves_previous(tmp_path, config):
    save_fixture(tmp_path)
    backups = Backups(tmp_path)
    original = backups.create("daily", config.effective())
    with patch("app.backups.shutil.disk_usage", return_value=shutil._ntuple_diskusage(100, 100, 0)):
        with pytest.raises(ValueError, match="Espaço"):
            backups.create("daily", config.effective())
    assert backups.resolve(original).exists()
    assert not list(backups.root.glob("*.partial"))


@pytest.mark.parametrize("name,kind", [("../escape", "file"), ("/absolute", "file"), ("saves/link", "symlink"),
    ("config/admin.json", "file"), ("saves/a", "duplicate"), ("saves/../../escape", "file")])
def test_reject_malicious_archives(tmp_path, name, kind):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(member)
        else:
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
            if kind == "duplicate":
                archive.addfile(member, io.BytesIO(b"y"))
    with pytest.raises((ValueError, KeyError)):
        Backups(tmp_path).validate(path)


def test_tampering_prevents_restore(tmp_path, config):
    saves = save_fixture(tmp_path)
    backups = Backups(tmp_path)
    name = backups.create("manual", config.effective())
    with backups.resolve(name).open("ab") as file:
        file.write(b"tampered")
    with pytest.raises(ValueError, match="Checksum"):
        backups.restore(name)
    assert (saves / "Dedicated.db").read_bytes() == b"world-data"


def test_retention_keeps_daily_and_weekly(tmp_path, config):
    save_fixture(tmp_path)
    backups = Backups(tmp_path)
    for stamp in ("20261001", "20261008", "20261015", "20261022", "20261029", "20261112", "20261113"):
        name = backups.create("daily", config.effective())
        original = backups.resolve(name)
        new = backups.root / (stamp + "T050000000000Z.tar.gz")
        original.rename(new)
        original.with_suffix(original.suffix + ".sha256").rename(new.with_suffix(new.suffix + ".sha256"))
    backups.prune(2, 2)
    names = [x["name"][:8] for x in backups.list()]
    assert names == ["20261113", "20261112", "20261029"]
