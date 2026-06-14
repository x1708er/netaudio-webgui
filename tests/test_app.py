import pytest
from fastapi.testclient import TestClient

from netaudio_webgui.app import create_app
from netaudio_webgui.config import Settings


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

    def set_device_name(self, host, new_name):
        self.calls.append(("name", host, new_name))

    def set_channel_name(self, host, number, new_name, channel_type):
        self.calls.append(("channel_name", host, number, new_name, channel_type))

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


def _app(token=None):
    fake = FakeClient()
    settings = Settings(bind="127.0.0.1", port=1, token=token, demo=False,
                        netaudio_bin="netaudio", discovery_timeout=2.0)
    app = create_app(settings=settings, client=fake)
    return app, fake


def test_state_endpoint_returns_state():
    app, _ = _app()
    client = TestClient(app)
    resp = client.get("/api/state")
    assert resp.status_code == 200
    assert resp.json()["devices"][0]["name"] == "A32"


def test_index_served():
    app, _ = _app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_token_required_when_set():
    app, _ = _app(token="secret")
    client = TestClient(app)
    assert client.get("/api/state").status_code == 401
    ok = client.get("/api/state", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


def test_channel_name_route_calls_client():
    app, fake = _app()
    client = TestClient(app)
    resp = client.put("/api/device/10.0.0.1/channel/2/name",
                      json={"name": "Vocals", "type": "rx"})
    assert resp.status_code == 200
    assert ("channel_name", "10.0.0.1", 2, "Vocals", "rx") in fake.calls


def test_add_subscription_calls_client():
    app, fake = _app()
    client = TestClient(app)
    resp = client.post("/api/subscription", json={
        "tx_device": "Inferno", "tx_number": 1, "rx_device": "A32", "rx_number": 2})
    assert resp.status_code == 200
    assert fake.calls[0] == ("add", {"tx_device": "Inferno", "tx_number": 1,
                                     "rx_device": "A32", "rx_number": 2})


def test_remove_subscription_calls_client():
    app, fake = _app()
    client = TestClient(app)
    resp = client.request("DELETE", "/api/subscription",
                          json={"rx_device": "A32", "rx_number": 2})
    assert resp.status_code == 200
    assert fake.calls[0] == ("remove", {"rx_device": "A32", "rx_number": 2})


def test_identify_calls_client():
    app, fake = _app()
    client = TestClient(app)
    assert client.post("/api/device/10.0.0.1/identify").status_code == 200
    assert ("identify", "10.0.0.1") in fake.calls


def test_netaudio_error_maps_to_502():
    app, fake = _app()

    def boom(**kwargs):
        from netaudio_webgui.netaudio_client import NetaudioError
        raise NetaudioError("Error: RX device not found.")

    fake.add_subscription = boom
    client = TestClient(app)
    resp = client.post("/api/subscription", json={
        "tx_device": "I", "tx_number": 1, "rx_device": "X", "rx_number": 1})
    assert resp.status_code == 502
    assert "not found" in resp.json()["detail"]


def test_reboot_calls_client():
    app, fake = _app()
    client = TestClient(app)
    assert client.post("/api/device/10.0.0.1/reboot").status_code == 200
    assert ("reboot", "10.0.0.1") in fake.calls


def test_rescan_calls_client_force_refresh():
    app, fake = _app()
    client = TestClient(app)
    assert client.post("/api/rescan").status_code == 200
    assert ("force_refresh",) in fake.calls
