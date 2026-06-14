import json
import subprocess

import pytest

from netaudio_webgui import netaudio_client as nc


def test_state_argv():
    assert nc.build_state_argv("netaudio", 2.0) == [
        "netaudio", "--timeout", "2.0", "--output", "json", "device", "list",
    ]


def test_add_subscription_argv():
    argv = nc.build_add_subscription_argv("netaudio", "Inferno", 1, "A32", 2)
    assert argv == [
        "netaudio", "subscription", "add",
        "--tx", "1@Inferno", "--rx", "2@A32",
    ]


def test_remove_subscription_argv():
    argv = nc.build_remove_subscription_argv("netaudio", "A32", 2)
    assert argv == ["netaudio", "subscription", "remove", "--rx", "2@A32"]


def test_bulk_subscription_argv():
    argv = nc.build_bulk_subscription_argv("netaudio", "Inferno", "A32",
                                           count=2, offset_tx=1, offset_rx=3)
    assert argv == [
        "netaudio", "subscription", "add",
        "--tx", "Inferno", "--rx", "A32",
        "--count", "2", "--offset-tx", "1", "--offset-rx", "3",
    ]


def test_bulk_subscription_argv_defaults():
    argv = nc.build_bulk_subscription_argv("netaudio", "Inferno", "A32")
    assert argv == [
        "netaudio", "subscription", "add",
        "--tx", "Inferno", "--rx", "A32",
        "--count", "0", "--offset-tx", "0", "--offset-rx", "0",
    ]


def test_device_name_argv():
    argv = nc.build_device_name_argv("netaudio", "192.168.178.50", "NewName")
    assert argv == ["netaudio", "--host", "192.168.178.50", "device", "name", "NewName"]


def test_channel_name_argv():
    argv = nc.build_channel_name_argv("netaudio", "192.168.178.50", 2, "Vocals", "rx")
    assert argv == [
        "netaudio", "--host", "192.168.178.50",
        "channel", "name", "2", "Vocals", "--type", "rx",
    ]


def test_identify_argv():
    assert nc.build_identify_argv("netaudio", "192.168.178.50") == [
        "netaudio", "--host", "192.168.178.50", "device", "identify",
    ]


def test_reboot_argv():
    assert nc.build_reboot_argv("netaudio", "192.168.178.50") == [
        "netaudio", "--host", "192.168.178.50", "device", "reboot",
    ]


def test_sample_rate_argv():
    assert nc.build_sample_rate_argv("netaudio", "192.168.178.50", 96000) == [
        "netaudio", "--host", "192.168.178.50", "device", "config", "sample-rate", "96000",
    ]


def test_encoding_argv():
    assert nc.build_encoding_argv("netaudio", "192.168.178.50", 24) == [
        "netaudio", "--host", "192.168.178.50", "device", "config", "encoding", "24",
    ]


def test_latency_argv():
    assert nc.build_latency_argv("netaudio", "192.168.178.50", 1.5) == [
        "netaudio", "--host", "192.168.178.50", "device", "config", "latency", "1.5",
    ]


def test_aes67_argv():
    assert nc.build_aes67_argv("netaudio", "192.168.178.50", True) == [
        "netaudio", "--host", "192.168.178.50", "device", "config", "aes67", "on",
    ]
    assert nc.build_aes67_argv("netaudio", "192.168.178.50", False)[-1] == "off"


def test_preferred_leader_argv():
    assert nc.build_preferred_leader_argv("netaudio", "192.168.178.50", True) == [
        "netaudio", "--host", "192.168.178.50", "device", "config", "preferred-leader", "on",
    ]
    assert nc.build_preferred_leader_argv("netaudio", "192.168.178.50", False)[-1] == "off"


def test_channel_gain_argv():
    assert nc.build_channel_gain_argv("netaudio", "192.168.178.50", 2, 4, "tx") == [
        "netaudio", "--host", "192.168.178.50",
        "channel", "gain", "2", "4", "--type", "tx",
    ]


DEVICE_LIST_JSON = {
    "A32-xxxx": {
        "channels": {
            "receivers": {"1": {"name": "01"}, "2": {"name": "02", "friendly_name": "Vocals"}},
            "transmitters": {},
        },
        "ipv4": "192.168.178.50", "name": "A32", "online": True, "server_name": "A32-xxxx",
        "subscriptions": [
            {"rx_channel": "01", "rx_device": "A32", "tx_channel": "L", "tx_device": "Inferno",
             "status": {"code": 9, "state": "connected", "label": "Connected", "detail": None}}
        ],
        "ptp_v1_role": "follower", "sample_rate": 48000, "model": "A32",
    },
    "Inferno-yyyy": {
        "channels": {
            "receivers": {},
            "transmitters": {"1": {"name": "L"}, "2": {"name": "R"}},
        },
        "ipv4": "192.168.178.51", "name": "Inferno", "online": True, "server_name": "Inferno-yyyy",
        "subscriptions": [], "clock_role": "leader", "sample_rate": 48000,
    },
}


def test_parse_state_devices_sorted_with_channels():
    state = nc.parse_state(DEVICE_LIST_JSON)
    names = [d["name"] for d in state["devices"]]
    assert names == ["A32", "Inferno"]  # sorted by name

    a32 = state["devices"][0]
    assert a32["ipv4"] == "192.168.178.50"
    assert a32["clock_role"] == "follower"
    assert a32["tx_channels"] == []
    assert a32["rx_channels"] == [
        {"number": 1, "name": "01", "label": "01"},
        {"number": 2, "name": "02", "label": "Vocals"},
    ]

    inferno = state["devices"][1]
    assert inferno["clock_role"] == "leader"
    assert inferno["tx_channels"] == [
        {"number": 1, "name": "L", "label": "L"},
        {"number": 2, "name": "R", "label": "R"},
    ]


def test_parse_state_subscriptions_and_leader():
    state = nc.parse_state(DEVICE_LIST_JSON)
    assert state["subscriptions"] == [
        {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno",
         "tx_channel": "L", "state": "connected", "label": "Connected"}
    ]
    assert state["leader"] == "Inferno"


def test_parse_state_empty():
    state = nc.parse_state({})
    assert state == {"devices": [], "subscriptions": [], "leader": None}


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _client(monkeypatch, returncode=0, stdout="", stderr="", capture=None):
    # relay mode keeps mutation tests fast (no real daemon restart)
    client = nc.NetaudioClient(netaudio_bin="netaudio", discovery_timeout=2.0,
                               restart_on_change=False)

    def fake_run(argv, **kwargs):
        if capture is not None:
            capture.append(argv)
        return _FakeCompleted(returncode, stdout, stderr)

    monkeypatch.setattr(nc.subprocess, "run", fake_run)
    # Don't let mutation tests hit the real daemon relay over the network.
    monkeypatch.setattr(client, "refresh", lambda *a, **k: None)
    return client


def test_get_state_parses_stdout(monkeypatch):
    client = _client(monkeypatch, stdout=json.dumps(DEVICE_LIST_JSON))
    state = client.get_state()
    assert [d["name"] for d in state["devices"]] == ["A32", "Inferno"]


def test_get_state_empty_output(monkeypatch):
    # No devices: netaudio prints non-JSON / nothing in json mode -> treat as empty
    client = _client(monkeypatch, stdout="")
    state = client.get_state()
    assert state == {"devices": [], "subscriptions": [], "leader": None}


def test_add_subscription_builds_command(monkeypatch):
    capture = []
    client = _client(monkeypatch, capture=capture)
    client.add_subscription(tx_device="Inferno", tx_number=1, rx_device="A32", rx_number=2)
    assert capture[0] == ["netaudio", "subscription", "add", "--tx", "1@Inferno", "--rx", "2@A32"]


def test_set_sample_rate_builds_command(monkeypatch):
    capture = []
    client = _client(monkeypatch, capture=capture)
    client.set_sample_rate("192.168.178.50", 96000)
    assert capture[0] == [
        "netaudio", "--host", "192.168.178.50", "device", "config", "sample-rate", "96000",
    ]


def test_set_sample_rate_rejects_bad_rate(monkeypatch):
    capture = []
    client = _client(monkeypatch, capture=capture)
    with pytest.raises(ValueError):
        client.set_sample_rate("192.168.178.50", 12345)
    assert capture == []  # validation happens before any subprocess call


def test_set_encoding_rejects_bad_bits(monkeypatch):
    capture = []
    client = _client(monkeypatch, capture=capture)
    with pytest.raises(ValueError):
        client.set_encoding("192.168.178.50", 8)
    assert capture == []


def test_set_latency_rejects_non_positive(monkeypatch):
    client = _client(monkeypatch)
    with pytest.raises(ValueError):
        client.set_latency("192.168.178.50", 0)


def test_set_channel_gain_builds_command(monkeypatch):
    capture = []
    client = _client(monkeypatch, capture=capture)
    client.set_channel_gain("192.168.178.50", 2, 4, "rx")
    assert capture[0] == [
        "netaudio", "--host", "192.168.178.50", "channel", "gain", "2", "4", "--type", "rx",
    ]


def test_set_channel_gain_rejects_out_of_range(monkeypatch):
    capture = []
    client = _client(monkeypatch, capture=capture)
    with pytest.raises(ValueError):
        client.set_channel_gain("192.168.178.50", 1, 6, "tx")
    assert capture == []


def test_set_channel_gain_rejects_bad_type(monkeypatch):
    client = _client(monkeypatch)
    with pytest.raises(ValueError):
        client.set_channel_gain("192.168.178.50", 1, 3, "mid")


def test_mutation_raises_on_error(monkeypatch):
    client = _client(monkeypatch, returncode=1, stderr="Error: RX device 'X' not found.")
    with pytest.raises(nc.NetaudioError) as exc:
        client.add_subscription(tx_device="I", tx_number=1, rx_device="X", rx_number=1)
    assert "not found" in str(exc.value)


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_refresh_all_posts_empty_body(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_method(), request.data))
        return _FakeResp()

    monkeypatch.setattr(nc.urllib.request, "urlopen", fake_urlopen)
    nc.NetaudioClient("netaudio", 2.0, relay_host="127.0.0.1", relay_port=9000).refresh()
    assert calls == [("http://127.0.0.1:9000/refresh", "POST", b"")]


def test_refresh_device_posts_device_body(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, request.data))
        return _FakeResp()

    monkeypatch.setattr(nc.urllib.request, "urlopen", fake_urlopen)
    nc.NetaudioClient("netaudio", 2.0).refresh(device="A32")
    assert calls[0][0].endswith("/refresh")
    assert json.loads(calls[0][1]) == {"device": "A32"}


def test_refresh_swallows_errors(monkeypatch):
    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(nc.urllib.request, "urlopen", boom)
    nc.NetaudioClient("netaudio", 2.0).refresh()  # must not raise


def test_add_subscription_refreshes_rx_device(monkeypatch):
    client = _client(monkeypatch)
    seen = []
    monkeypatch.setattr(client, "refresh", lambda device=None: seen.append(device))
    client.add_subscription(tx_device="Inferno", tx_number=1, rx_device="A32", rx_number=2)
    assert seen == ["A32"]


def test_remove_subscription_refreshes_rx_device(monkeypatch):
    client = _client(monkeypatch)
    seen = []
    monkeypatch.setattr(client, "refresh", lambda device=None: seen.append(device))
    client.remove_subscription(rx_device="A32", rx_number=2)
    assert seen == ["A32"]


def test_rescan_refreshes_each_device_by_name(monkeypatch):
    client = nc.NetaudioClient("netaudio", 2.0, restart_on_change=False)
    monkeypatch.setattr(client, "get_state",
                        lambda: {"devices": [{"name": "A32"}, {"name": "Inferno"}],
                                 "subscriptions": [], "leader": None})
    seen = []
    monkeypatch.setattr(client, "refresh", lambda device=None: seen.append(device))
    client.rescan()
    assert seen == ["A32", "Inferno"]


def test_mutation_restarts_daemon_when_enabled(monkeypatch):
    client = nc.NetaudioClient("netaudio", 2.0, restart_on_change=True)
    monkeypatch.setattr(nc.subprocess, "run",
                        lambda argv, **kw: _FakeCompleted(0, "", ""))  # CLI remove succeeds
    restarts = []
    monkeypatch.setattr(client, "restart_daemon", lambda: restarts.append(True))
    client.remove_subscription(rx_device="A32", rx_number=2)
    assert restarts == [True]


def test_restart_daemon_waits_for_devices(monkeypatch):
    client = nc.NetaudioClient("netaudio", 2.0, restart_on_change=True)

    def fake_run(argv, **kwargs):
        if argv[-1] == "status":
            return _FakeCompleted(0, "Daemon is running. 2 device(s) cached.", "")
        return _FakeCompleted(0, "Daemon started in the background.", "")  # restart

    slept = []
    monkeypatch.setattr(nc.subprocess, "run", fake_run)
    monkeypatch.setattr(nc.time, "sleep", lambda s: slept.append(s))
    client.restart_daemon()
    assert slept == []  # devices reported cached on first status poll -> no waiting


def test_force_refresh_restarts_when_enabled(monkeypatch):
    client = nc.NetaudioClient("netaudio", 2.0, restart_on_change=True)
    restarts = []
    monkeypatch.setattr(client, "restart_daemon", lambda: restarts.append(True))
    client.force_refresh()
    assert restarts == [True]


def test_parse_state_subscription_resolves_friendly_labels():
    data = {
        "rx-dev": {
            "channels": {"receivers": {"2": {"name": "02", "friendly_name": "Vocals"}}, "transmitters": {}},
            "ipv4": "1.1.1.1", "name": "RX", "online": True, "server_name": "rx-dev",
            "subscriptions": [
                {"rx_channel": "02", "rx_device": "RX", "tx_channel": "AesL", "tx_device": "TX",
                 "status": {"state": "connected", "label": "Connected"}}
            ],
        },
        "tx-dev": {
            "channels": {"receivers": {}, "transmitters": {"1": {"name": "AesL", "friendly_name": "Main-L"}}},
            "ipv4": "2.2.2.2", "name": "TX", "online": True, "server_name": "tx-dev",
            "subscriptions": [],
        },
    }
    state = nc.parse_state(data)
    sub = state["subscriptions"][0]
    # Raw channel names ("02","AesL") must be resolved to the display labels the matrix uses.
    assert sub["rx_channel"] == "Vocals"
    assert sub["tx_channel"] == "Main-L"
    # And the rx channel's label is what the matrix renders.
    rx_dev = next(d for d in state["devices"] if d["name"] == "RX")
    assert rx_dev["rx_channels"][0]["label"] == "Vocals"


def test_parse_state_decodes_known_connected_status_codes():
    # netaudio v0.2.5 ships an empty status catalog, so real Dante status codes
    # arrive undecoded (state="unknown"). Code 9 ("dynamic"/unicast) is an
    # established connection -> the matrix must show it green, not amber.
    data = {
        "rx": {
            "channels": {"receivers": {"1": {"name": "01"}}, "transmitters": {}},
            "ipv4": "1.1.1.1", "name": "RX", "online": True, "server_name": "rx",
            "subscriptions": [
                {"rx_channel": "01", "rx_device": "RX", "tx_channel": "L", "tx_device": "TX",
                 "status": {"code": 9, "state": "unknown", "label": "Unknown (9)"}}
            ],
        },
    }
    sub = nc.parse_state(data)["subscriptions"][0]
    assert sub["state"] == "connected"


def test_parse_state_keeps_netaudio_decoded_state():
    # When netaudio DOES decode the status, trust it (don't override).
    data = {
        "rx": {
            "channels": {"receivers": {"1": {"name": "01"}}, "transmitters": {}},
            "ipv4": "1.1.1.1", "name": "RX", "online": True, "server_name": "rx",
            "subscriptions": [
                {"rx_channel": "01", "rx_device": "RX", "tx_channel": "L", "tx_device": "TX",
                 "status": {"code": 9, "state": "error", "label": "Some error"}}
            ],
        },
    }
    sub = nc.parse_state(data)["subscriptions"][0]
    assert sub["state"] == "error"
