import pytest
from fastapi.testclient import TestClient

from netaudio_webgui.app import create_app
from netaudio_webgui.auth import UserStore
from netaudio_webgui.config import Settings
from netaudio_webgui.presets import PresetStore
from netaudio_webgui.zones import ZoneStore


class FakeClient:
    def __init__(self):
        self.calls = []
        self.state = {"devices": [{"name": "A32", "ipv4": "10.0.0.1", "server_name": "A32",
                                    "online": True, "model": "x", "sample_rate": 48000,
                                    "clock_role": "follower", "tx_channels": [], "rx_channels": []}],
                      "subscriptions": [], "leader": None}

    def get_state(self):
        return self.state

    def add_subscription(self, **kwargs):
        self.calls.append(("add", kwargs))

    def remove_subscription(self, **kwargs):
        self.calls.append(("remove", kwargs))

    def add_bulk_subscription(self, **kwargs):
        self.calls.append(("bulk", kwargs))

    def set_device_name(self, host, new_name):
        self.calls.append(("name", host, new_name))

    def set_channel_name(self, host, number, new_name, channel_type):
        self.calls.append(("channel_name", host, number, new_name, channel_type))

    def set_sample_rate(self, host, rate):
        if rate not in {44100, 48000, 88200, 96000, 176400, 192000}:
            raise ValueError(f"invalid sample rate: {rate}")
        self.calls.append(("sample_rate", host, rate))

    def set_encoding(self, host, bits):
        if bits not in {16, 24, 32}:
            raise ValueError(f"invalid encoding: {bits}")
        self.calls.append(("encoding", host, bits))

    def set_latency(self, host, value):
        if float(value) <= 0:
            raise ValueError(f"invalid latency: {value}")
        self.calls.append(("latency", host, value))

    def set_aes67(self, host, enabled):
        self.calls.append(("aes67", host, enabled))

    def set_preferred_leader(self, host, enabled):
        self.calls.append(("preferred_leader", host, enabled))

    def set_channel_gain(self, host, number, level, channel_type):
        if channel_type not in ("tx", "rx"):
            raise ValueError(f"invalid channel type: {channel_type}")
        if level not in {1, 2, 3, 4, 5}:
            raise ValueError(f"invalid gain level: {level}")
        self.calls.append(("gain", host, number, level, channel_type))

    def identify(self, host):
        self.calls.append(("identify", host))

    def reboot(self, host):
        self.calls.append(("reboot", host))

    def refresh(self, device=None):
        self.calls.append(("refresh", device))

    def rescan(self):
        self.calls.append(("rescan",))

    def force_refresh(self):
        self.calls.append(("force_refresh",))


def _app(users=None):
    fake = FakeClient()
    settings = Settings(bind="127.0.0.1", port=1, demo=False,
                        netaudio_bin="netaudio", discovery_timeout=2.0)
    users = users if users is not None else UserStore.from_plaintext({"test": "test"})
    app = create_app(settings=settings, client=fake, users=users)
    return app, fake


def _client(app, username="test", password="test"):
    """A TestClient that has logged in (TestClient persists the session cookie)."""
    client = TestClient(app)
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _routing_state(subscriptions):
    """A state with two devices + channels so label->number resolution works."""
    return {
        "devices": [
            {"name": "Inferno", "ipv4": "10.0.0.2", "server_name": "Inferno",
             "online": True, "model": "x", "sample_rate": 48000, "clock_role": "leader",
             "tx_channels": [{"number": 1, "name": "L", "label": "L"},
                             {"number": 2, "name": "R", "label": "R"}],
             "rx_channels": [{"number": 1, "name": "01", "label": "01"},
                             {"number": 2, "name": "02", "label": "02"}]},
            {"name": "A32", "ipv4": "10.0.0.1", "server_name": "A32",
             "online": True, "model": "x", "sample_rate": 48000, "clock_role": "follower",
             "tx_channels": [{"number": 1, "name": "Mic1", "label": "Mic1"}],
             "rx_channels": [{"number": 1, "name": "01", "label": "01"},
                             {"number": 2, "name": "02", "label": "02"}]},
        ],
        "subscriptions": subscriptions,
        "leader": "Inferno",
    }


def _preset_app(tmp_path, subscriptions=None):
    fake = FakeClient()
    fake.state = _routing_state(subscriptions or [])
    settings = Settings(bind="127.0.0.1", port=1, demo=False,
                        netaudio_bin="netaudio", discovery_timeout=2.0)
    store = PresetStore(tmp_path / "presets.json")
    users = UserStore.from_plaintext({"test": "test"})
    app = create_app(settings=settings, client=fake, store=store, users=users)
    return app, fake, store


def _zone_app(tmp_path, subscriptions=None, zones_config=None, presets=None):
    fake = FakeClient()
    fake.state = _routing_state(subscriptions or [])
    settings = Settings(bind="127.0.0.1", port=1, demo=False,
                        netaudio_bin="netaudio", discovery_timeout=2.0)
    store = PresetStore(tmp_path / "presets.json")
    for name, subs in (presets or {}).items():
        store.save(name, subs)
    zones = ZoneStore(tmp_path / "zones.json")
    if zones_config is not None:
        zones.save(zones_config)
    users = UserStore.from_plaintext({"test": "test"})
    app = create_app(settings=settings, client=fake, store=store, users=users, zones=zones)
    return app, fake, store, zones


def test_state_endpoint_returns_state():
    app, _ = _app()
    client = _client(app)
    resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["devices"][0]["name"] == "A32"


def test_index_served():
    app, _ = _app()
    client = _client(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_protected_endpoint_needs_login():
    app, _ = _app()
    client = TestClient(app)  # not logged in
    assert client.get("/api/state").status_code == 401


def test_login_success_then_protected_ok():
    app, _ = _app()
    client = TestClient(app)
    assert client.post("/api/login", json={"username": "test", "password": "test"}).status_code == 200
    assert client.get("/api/state").status_code == 200


def test_login_bad_credentials_401():
    app, _ = _app()
    client = TestClient(app)
    assert client.post("/api/login", json={"username": "test", "password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"username": "ghost", "password": "x"}).status_code == 401


def test_me_returns_username():
    app, _ = _app()
    client = _client(app)
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "test"


def test_logout_clears_session():
    app, _ = _app()
    client = _client(app)
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/state").status_code == 401


def test_channel_name_route_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/channel/2/name",
                      json={"name": "Vocals", "type": "rx"})
    assert resp.status_code == 200
    assert ("channel_name", "10.0.0.1", 2, "Vocals", "rx") in fake.calls


def test_config_sample_rate_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/sample-rate", json={"value": 96000})
    assert resp.status_code == 200
    assert ("sample_rate", "10.0.0.1", 96000) in fake.calls


def test_config_encoding_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/encoding", json={"value": 24})
    assert resp.status_code == 200
    assert ("encoding", "10.0.0.1", 24) in fake.calls


def test_config_latency_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/latency", json={"value": 2})
    assert resp.status_code == 200
    assert ("latency", "10.0.0.1", 2) in fake.calls


def test_config_latency_accepts_float():
    # The latency control sends a float; the body model must not reject it.
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/latency", json={"value": 2.5})
    assert resp.status_code == 200
    assert ("latency", "10.0.0.1", 2.5) in fake.calls


def test_config_aes67_bool_not_coerced_to_int():
    # JSON true/false must stay bool (not become 1/0 via the int union member).
    app, fake = _app()
    client = _client(app)
    client.put("/api/device/10.0.0.1/config/aes67", json={"value": True})
    client.put("/api/device/10.0.0.1/config/aes67", json={"value": False})
    assert ("aes67", "10.0.0.1", True) in fake.calls
    assert ("aes67", "10.0.0.1", False) in fake.calls


def test_config_aes67_coerces_string_to_bool():
    app, fake = _app()
    client = _client(app)
    assert client.put("/api/device/10.0.0.1/config/aes67", json={"value": "on"}).status_code == 200
    assert ("aes67", "10.0.0.1", True) in fake.calls
    assert client.put("/api/device/10.0.0.1/config/aes67", json={"value": "off"}).status_code == 200
    assert ("aes67", "10.0.0.1", False) in fake.calls


def test_config_preferred_leader_accepts_bool():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/preferred-leader", json={"value": True})
    assert resp.status_code == 200
    assert ("preferred_leader", "10.0.0.1", True) in fake.calls


def test_config_invalid_value_400():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/sample-rate", json={"value": 12345})
    assert resp.status_code == 400
    assert fake.calls == []


def test_config_invalid_bool_400():
    app, _ = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/aes67", json={"value": "maybe"})
    assert resp.status_code == 400


def test_config_unknown_key_404():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/config/bogus", json={"value": 1})
    assert resp.status_code == 404
    assert fake.calls == []


def test_channel_gain_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/channel/2/gain", json={"level": 4, "type": "tx"})
    assert resp.status_code == 200
    assert ("gain", "10.0.0.1", 2, 4, "tx") in fake.calls


def test_channel_gain_out_of_range_400():
    app, fake = _app()
    client = _client(app)
    resp = client.put("/api/device/10.0.0.1/channel/2/gain", json={"level": 6, "type": "tx"})
    assert resp.status_code == 400
    assert fake.calls == []


def test_add_subscription_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.post("/api/subscription", json={
        "tx_device": "Inferno", "tx_number": 1, "rx_device": "A32", "rx_number": 2})
    assert resp.status_code == 200
    assert fake.calls[0] == ("add", {"tx_device": "Inferno", "tx_number": 1,
                                     "rx_device": "A32", "rx_number": 2})


def test_remove_subscription_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.request("DELETE", "/api/subscription",
                          json={"rx_device": "A32", "rx_number": 2})
    assert resp.status_code == 200
    assert fake.calls[0] == ("remove", {"rx_device": "A32", "rx_number": 2})


def test_bulk_subscription_calls_client():
    app, fake = _app()
    client = _client(app)
    resp = client.post("/api/subscription/bulk", json={
        "tx_device": "Inferno", "rx_device": "A32",
        "count": 2, "offset_tx": 1, "offset_rx": 0})
    assert resp.status_code == 200
    assert fake.calls[0] == ("bulk", {"tx_device": "Inferno", "rx_device": "A32",
                                      "count": 2, "offset_tx": 1, "offset_rx": 0})


def test_bulk_subscription_defaults():
    app, fake = _app()
    client = _client(app)
    resp = client.post("/api/subscription/bulk", json={
        "tx_device": "Inferno", "rx_device": "A32"})
    assert resp.status_code == 200
    assert fake.calls[0] == ("bulk", {"tx_device": "Inferno", "rx_device": "A32",
                                      "count": 0, "offset_tx": 0, "offset_rx": 0})


def test_identify_calls_client():
    app, fake = _app()
    client = _client(app)
    assert client.post("/api/device/10.0.0.1/identify").status_code == 200
    assert ("identify", "10.0.0.1") in fake.calls


def test_netaudio_error_maps_to_502():
    app, fake = _app()

    def boom(**kwargs):
        from netaudio_webgui.netaudio_client import NetaudioError
        raise NetaudioError("Error: RX device not found.")

    fake.add_subscription = boom
    client = _client(app)
    resp = client.post("/api/subscription", json={
        "tx_device": "I", "tx_number": 1, "rx_device": "X", "rx_number": 1})
    assert resp.status_code == 502
    assert "not found" in resp.json()["detail"]


def test_reboot_calls_client():
    app, fake = _app()
    client = _client(app)
    assert client.post("/api/device/10.0.0.1/reboot").status_code == 200
    assert ("reboot", "10.0.0.1") in fake.calls


def test_rescan_calls_client_force_refresh():
    app, fake = _app()
    client = _client(app)
    assert client.post("/api/rescan").status_code == 200
    assert ("force_refresh",) in fake.calls


# ---- presets / scenes ----------------------------------------------------

_SUB = {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L",
        "state": "connected", "label": "Connected"}


def test_save_preset_snapshots_current_subs(tmp_path):
    app, _, store = _preset_app(tmp_path, [_SUB])
    client = _client(app)
    resp = client.post("/api/presets", json={"name": "Show A"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "count": 1}
    # Stored snapshot keeps only the four routing fields.
    assert store.get("Show A") == [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ]


def test_list_presets(tmp_path):
    app, _, store = _preset_app(tmp_path)
    store.save("alpha", [])
    store.save("zebra", [])
    client = _client(app)
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    assert resp.json() == {"presets": ["alpha", "zebra"]}


def test_apply_preset_records_add_and_remove(tmp_path):
    # Current live state: A32/02 <- Inferno/R. Desired preset: A32/01 <- Inferno/L.
    current = [{"rx_device": "A32", "rx_channel": "02", "tx_device": "Inferno", "tx_channel": "R",
                "state": "connected", "label": "Connected"}]
    app, fake, store = _preset_app(tmp_path, current)
    store.save("Show A", [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ])
    client = _client(app)
    resp = client.post("/api/presets/Show A/apply")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "added": 1, "removed": 1, "skipped": 0}
    assert ("add", {"tx_device": "Inferno", "tx_number": 1,
                    "rx_device": "A32", "rx_number": 1}) in fake.calls
    assert ("remove", {"rx_device": "A32", "rx_number": 2}) in fake.calls


def test_apply_preset_repoint_keeps_new_subscription(tmp_path):
    # Regression: re-pointing an already-subscribed RX channel to a different TX
    # must NOT remove that channel. Current: A32/01 <- Inferno/R. Desired:
    # A32/01 <- Inferno/L (same RX channel, different TX). Since Dante allows one
    # subscription per RX channel, the add overwrites the old sub; issuing a
    # remove for A32/01 afterwards would nuke the just-set subscription.
    current = [{"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "R",
                "state": "connected", "label": "Connected"}]
    app, fake, store = _preset_app(tmp_path, current)
    store.save("Repoint", [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ])
    client = _client(app)
    resp = client.post("/api/presets/Repoint/apply")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "added": 1, "removed": 0, "skipped": 0}
    assert ("add", {"tx_device": "Inferno", "tx_number": 1,
                    "rx_device": "A32", "rx_number": 1}) in fake.calls
    assert ("remove", {"rx_device": "A32", "rx_number": 1}) not in fake.calls


def test_apply_preset_skips_unresolvable(tmp_path):
    app, fake, store = _preset_app(tmp_path, [])
    store.save("Ghosts", [
        {"rx_device": "Ghost", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ])
    client = _client(app)
    resp = client.post("/api/presets/Ghosts/apply")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "added": 0, "removed": 0, "skipped": 1}
    assert fake.calls == []


def test_delete_preset(tmp_path):
    app, _, store = _preset_app(tmp_path)
    store.save("Show A", [])
    client = _client(app)
    resp = client.request("DELETE", "/api/presets/Show A")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert store.list() == []


def test_apply_missing_preset_404(tmp_path):
    app, _, _ = _preset_app(tmp_path)
    client = _client(app)
    assert client.post("/api/presets/nope/apply").status_code == 404


def test_delete_missing_preset_404(tmp_path):
    app, _, _ = _preset_app(tmp_path)
    client = _client(app)
    assert client.request("DELETE", "/api/presets/nope").status_code == 404


def test_save_empty_name_400(tmp_path):
    app, _, _ = _preset_app(tmp_path)
    client = _client(app)
    assert client.post("/api/presets", json={"name": "   "}).status_code == 400


# ---- config export / import ---------------------------------------------


def test_import_subscriptions_records_add_and_remove(tmp_path):
    # Current live state: A32/02 <- Inferno/R. Imported: A32/01 <- Inferno/L.
    current = [{"rx_device": "A32", "rx_channel": "02", "tx_device": "Inferno", "tx_channel": "R",
                "state": "connected", "label": "Connected"}]
    app, fake, _ = _preset_app(tmp_path, current)
    client = _client(app)
    resp = client.post("/api/subscriptions/import", json={"subscriptions": [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "added": 1, "removed": 1, "skipped": 0}
    assert ("add", {"tx_device": "Inferno", "tx_number": 1,
                    "rx_device": "A32", "rx_number": 1}) in fake.calls
    assert ("remove", {"rx_device": "A32", "rx_number": 2}) in fake.calls


def test_import_subscriptions_skips_unresolvable(tmp_path):
    app, fake, _ = _preset_app(tmp_path, [])
    client = _client(app)
    resp = client.post("/api/subscriptions/import", json={"subscriptions": [
        {"rx_device": "Ghost", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
    ]})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "added": 0, "removed": 0, "skipped": 1}
    assert fake.calls == []


# ---- audit log -----------------------------------------------------------


def test_log_records_mutation_and_skips_get():
    app, _ = _app()
    client = _client(app)
    # A successful mutation should be logged.
    assert client.post("/api/subscription", json={
        "tx_device": "Inferno", "tx_number": 1, "rx_device": "A32", "rx_number": 2}).status_code == 200
    # A GET must NOT be logged.
    assert client.get("/api/state").status_code == 200
    entries = client.get("/api/log").json()["log"]
    posts = [e for e in entries if e["method"] == "POST" and e["path"] == "/api/subscription"]
    assert len(posts) == 1
    assert posts[0]["status"] == 200
    assert "ts" in posts[0]
    assert not any(e["method"] == "GET" for e in entries)


def test_log_records_errored_mutation_as_502():
    app, fake = _app()

    def boom(**kwargs):
        from netaudio_webgui.netaudio_client import NetaudioError
        raise NetaudioError("Error: RX device not found.")

    fake.add_subscription = boom
    client = _client(app)
    assert client.post("/api/subscription", json={
        "tx_device": "I", "tx_number": 1, "rx_device": "X", "rx_number": 1}).status_code == 502
    entries = client.get("/api/log").json()["log"]
    posts = [e for e in entries if e["path"] == "/api/subscription"]
    assert posts and posts[0]["status"] == 502


def test_create_app_without_users_fails_fast():
    settings = Settings(bind="127.0.0.1", port=1, demo=False,
                        netaudio_bin="netaudio", discovery_timeout=2.0,
                        users_path="/nonexistent/users.json")
    with pytest.raises(RuntimeError):
        create_app(settings=settings, client=FakeClient())


_L = {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L"}
_R = {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "R"}
_ZONES = {
    "master": {"buttons": ["L-Scene"], "off": True},
    "zones": [{"name": "Saal", "rx": [{"device": "A32", "channel": "01"}],
               "buttons": ["L-Scene", "R-Scene"], "off": True}],
}


def test_get_zones_returns_config(tmp_path):
    app, _, _, _ = _zone_app(tmp_path, zones_config=_ZONES)
    client = _client(app)
    assert client.get("/api/zones").json() == _ZONES


def test_put_zones_saves_and_validates(tmp_path):
    app, _, _, zones = _zone_app(tmp_path)
    client = _client(app)
    assert client.put("/api/zones", json=_ZONES).status_code == 200
    assert zones.load() == _ZONES
    assert client.put("/api/zones", json={"zones": [{"name": ""}]}).status_code == 400


def test_zone_apply_is_scoped(tmp_path):
    app, fake, _, _ = _zone_app(tmp_path, subscriptions=[dict(_L, state="connected", label="x")],
                                zones_config=_ZONES, presets={"R-Scene": [_R]})
    client = _client(app)
    resp = client.post("/api/zones/Saal/apply/R-Scene")
    assert resp.status_code == 200
    assert resp.json()["added"] == 1
    assert resp.json()["removed"] == 0
    assert ("add", {"tx_device": "Inferno", "tx_number": 2,
                    "rx_device": "A32", "rx_number": 1}) in fake.calls


def test_zone_off_clears_zone(tmp_path):
    app, fake, _, _ = _zone_app(tmp_path, subscriptions=[dict(_L, state="connected", label="x")],
                                zones_config=_ZONES)
    client = _client(app)
    resp = client.post("/api/zones/Saal/off")
    assert resp.status_code == 200
    assert ("remove", {"rx_device": "A32", "rx_number": 1}) in fake.calls


def test_zone_apply_unknown_zone_404(tmp_path):
    app, _, _, _ = _zone_app(tmp_path, zones_config=_ZONES, presets={"R-Scene": [_R]})
    client = _client(app)
    assert client.post("/api/zones/Ghost/apply/R-Scene").status_code == 404


def test_zone_apply_unknown_scene_404(tmp_path):
    app, _, _, _ = _zone_app(tmp_path, zones_config=_ZONES)
    client = _client(app)
    assert client.post("/api/zones/Saal/apply/Nope").status_code == 404


def test_master_apply_scopes_all_zones(tmp_path):
    app, fake, _, _ = _zone_app(tmp_path, zones_config=_ZONES, presets={"L-Scene": [_L]})
    client = _client(app)
    resp = client.post("/api/zones/apply/L-Scene")
    assert resp.status_code == 200
    assert ("add", {"tx_device": "Inferno", "tx_number": 1,
                    "rx_device": "A32", "rx_number": 1}) in fake.calls


def test_zones_state_reports_active_button(tmp_path):
    app, _, _, _ = _zone_app(tmp_path, subscriptions=[dict(_L, state="connected", label="x")],
                             zones_config=_ZONES, presets={"L-Scene": [_L], "R-Scene": [_R]})
    client = _client(app)
    body = client.get("/api/zones/state").json()
    assert body["zones"]["Saal"] == "L-Scene"


def test_zones_state_reports_off_when_empty(tmp_path):
    app, _, _, _ = _zone_app(tmp_path, subscriptions=[], zones_config=_ZONES,
                             presets={"L-Scene": [_L]})
    client = _client(app)
    body = client.get("/api/zones/state").json()
    assert body["zones"]["Saal"] == "off"


def test_master_off_clears_all_zones(tmp_path):
    app, fake, _, _ = _zone_app(tmp_path, subscriptions=[dict(_L, state="connected", label="x")],
                                zones_config=_ZONES)
    client = _client(app)
    resp = client.post("/api/zones/off")
    assert resp.status_code == 200
    assert ("remove", {"rx_device": "A32", "rx_number": 1}) in fake.calls


def test_zones_state_reports_master_active(tmp_path):
    # Master button "L-Scene" matches the union routing (only A32/01 <- Inferno/L).
    app, _, _, _ = _zone_app(tmp_path, subscriptions=[dict(_L, state="connected", label="x")],
                             zones_config=_ZONES, presets={"L-Scene": [_L]})
    client = _client(app)
    body = client.get("/api/zones/state").json()
    assert body["master"] == "L-Scene"
