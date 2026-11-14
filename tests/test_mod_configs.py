import pytest
from app.mod_configs import ModConfigs, Conflict


@pytest.fixture
def editor(tmp_path):
    editor = ModConfigs(tmp_path)
    editor.root.mkdir(parents=True)
    (editor.root / "plugin.cfg").write_text("[General]\nEnabled = true\n", encoding="utf-8")
    return editor


def test_edit_previous_and_conflict(editor):
    initial = editor.read("plugin.cfg")
    saved = editor.save("plugin.cfg", "Enabled = false\n", initial["revision"])
    assert saved["previous_available"]
    previous = editor.read("plugin.cfg", previous=True)
    assert previous["content"] == initial["content"]
    assert previous["revision"] == saved["revision"]
    with pytest.raises(Conflict):
        editor.save("plugin.cfg", "stale edit", initial["revision"])
    restored = editor.save("plugin.cfg", previous["content"], previous["revision"])
    assert restored["content"] == initial["content"]
    assert editor.list() == ["plugin.cfg"]


@pytest.mark.parametrize(
    "name",
    [
        "../admin.json",
        "/etc/passwd",
        "C:/data/a.cfg",
        "a\\b.cfg",
        "../config/plugin.cfg",
        "plugin.dll",
    ],
)
def test_paths_cannot_leave_configuration_folder(editor, name):
    with pytest.raises(ValueError):
        editor.read(name)


def test_binary_and_oversize_are_rejected(editor):
    (editor.root / "binary.cfg").write_bytes(b"a\x00b")
    with pytest.raises(ValueError):
        editor.read("binary.cfg")
    current = editor.read("plugin.cfg")
    with pytest.raises(ValueError):
        editor.save("plugin.cfg", "é" * 32768, current["revision"])
    assert editor.read("plugin.cfg")["revision"] == current["revision"]


def test_symlink_is_not_listed_or_read(editor, tmp_path):
    outside = tmp_path / "private.cfg"
    outside.write_text("secret")
    try:
        (editor.root / "link.cfg").symlink_to(outside)
    except OSError:
        pytest.skip("Criação de symlink indisponível")
    assert editor.list() == ["plugin.cfg"]
    with pytest.raises(ValueError):
        editor.read("link.cfg")
