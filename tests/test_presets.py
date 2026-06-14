import json

import pytest

from netaudio_webgui.presets import PresetStore


def _subs():
    return [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"},
        {"rx_device": "A32", "rx_channel": "02", "tx_device": "Inferno", "tx_channel": "R"},
    ]


def test_save_then_get_roundtrips(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save("Show A", _subs())
    assert store.get("Show A") == _subs()


def test_list_is_sorted(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save("zebra", [])
    store.save("alpha", [])
    store.save("mike", [])
    assert store.list() == ["alpha", "mike", "zebra"]


def test_save_upserts(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save("Scene", _subs())
    new = [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "R"}]
    store.save("Scene", new)
    assert store.list() == ["Scene"]
    assert store.get("Scene") == new


def test_save_does_not_create_dir_until_save(tmp_path):
    target = tmp_path / "nested" / "deep" / "presets.json"
    store = PresetStore(target)
    assert not target.parent.exists()  # no dir creation on init
    store.save("S", [])
    assert target.exists()
    assert target.parent.is_dir()


def test_missing_file_lists_empty(tmp_path):
    store = PresetStore(tmp_path / "absent.json")
    assert store.list() == []


def test_corrupt_file_tolerated(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text("{not valid json at all", encoding="utf-8")
    store = PresetStore(path)
    assert store.list() == []
    # And we can still save over it.
    store.save("Recovered", _subs())
    assert store.list() == ["Recovered"]


def test_file_format_is_presets_object(tmp_path):
    path = tmp_path / "presets.json"
    store = PresetStore(path)
    store.save("Show", _subs())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"presets"}
    assert data["presets"]["Show"] == _subs()


def test_save_strips_extra_keys(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save("S", [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno",
                      "tx_channel": "L", "state": "connected", "label": "Connected"}])
    assert store.get("S") == [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ]


def test_atomic_save_leaves_no_temp_files(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save("S", _subs())
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "presets.json"]
    assert leftovers == []


def test_empty_name_raises_value_error(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    with pytest.raises(ValueError):
        store.save("   ", _subs())
    with pytest.raises(ValueError):
        store.save("", _subs())


def test_get_missing_raises_key_error(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    with pytest.raises(KeyError):
        store.get("nope")


def test_delete_removes_and_missing_raises(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store.save("S", _subs())
    store.delete("S")
    assert store.list() == []
    with pytest.raises(KeyError):
        store.delete("S")
