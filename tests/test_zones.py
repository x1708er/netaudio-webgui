import json

import pytest

from netaudio_webgui.zones import ZoneStore

_CONFIG = {
    "master": {"buttons": ["Vortrag"], "off": True},
    "zones": [
        {"name": "Saal",
         "rx": [{"device": "A32", "channel": "01"}, {"device": "A32", "channel": "02"}],
         "buttons": ["Vortrag", "Musik"], "off": True},
    ],
}


def test_load_missing_file_is_empty(tmp_path):
    store = ZoneStore(tmp_path / "zones.json")
    assert store.load() == {"master": {"buttons": [], "off": False}, "zones": []}


def test_save_then_load_roundtrip(tmp_path):
    store = ZoneStore(tmp_path / "zones.json")
    store.save(_CONFIG)
    assert store.load() == _CONFIG


def test_save_normalizes_and_defaults(tmp_path):
    store = ZoneStore(tmp_path / "zones.json")
    store.save({"zones": [{"name": "Bar", "rx": [{"device": "A32", "channel": "01"}]}]})
    loaded = store.load()
    assert loaded["master"] == {"buttons": [], "off": False}
    assert loaded["zones"][0] == {"name": "Bar", "rx": [{"device": "A32", "channel": "01"}],
                                  "buttons": [], "off": False}


def test_save_rejects_empty_zone_name(tmp_path):
    store = ZoneStore(tmp_path / "zones.json")
    with pytest.raises(ValueError):
        store.save({"zones": [{"name": "  ", "rx": []}]})


def test_save_rejects_duplicate_zone_names(tmp_path):
    store = ZoneStore(tmp_path / "zones.json")
    with pytest.raises(ValueError):
        store.save({"zones": [{"name": "Saal", "rx": []}, {"name": "Saal", "rx": []}]})


def test_save_rejects_rx_without_device_or_channel(tmp_path):
    store = ZoneStore(tmp_path / "zones.json")
    with pytest.raises(ValueError):
        store.save({"zones": [{"name": "Saal", "rx": [{"device": "A32"}]}]})


def test_load_corrupt_file_is_empty(tmp_path):
    path = tmp_path / "zones.json"
    path.write_text("not json", encoding="utf-8")
    assert ZoneStore(path).load() == {"master": {"buttons": [], "off": False}, "zones": []}
