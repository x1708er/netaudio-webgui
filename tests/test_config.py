from netaudio_webgui.config import load_settings


def test_defaults(monkeypatch):
    for var in ("NETAUDIO_GUI_BIND", "NETAUDIO_GUI_PORT", "NETAUDIO_GUI_TOKEN",
                "NETAUDIO_GUI_DEMO", "NETAUDIO_BIN", "NETAUDIO_GUI_TIMEOUT",
                "NETAUDIO_RELAY_HOST", "NETAUDIO_RELAY_PORT",
                "NETAUDIO_GUI_RESTART_ON_CHANGE"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.bind == "0.0.0.0"
    assert s.port == 36342
    assert s.token is None
    assert s.demo is False
    assert s.netaudio_bin == "netaudio"
    assert s.discovery_timeout == 2.0
    assert s.relay_host == "127.0.0.1"
    assert s.relay_port == 9000
    assert s.restart_on_change is True


def test_restart_on_change_can_be_disabled(monkeypatch):
    monkeypatch.setenv("NETAUDIO_GUI_RESTART_ON_CHANGE", "0")
    assert load_settings().restart_on_change is False


def test_overrides(monkeypatch):
    monkeypatch.setenv("NETAUDIO_GUI_PORT", "9000")
    monkeypatch.setenv("NETAUDIO_GUI_TOKEN", "secret")
    monkeypatch.setenv("NETAUDIO_GUI_DEMO", "1")
    s = load_settings()
    assert s.port == 9000
    assert s.token == "secret"
    assert s.demo is True
