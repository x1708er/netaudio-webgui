from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    bind: str
    port: int
    demo: bool
    netaudio_bin: str
    discovery_timeout: float
    # netaudio daemon relay (used to force a cache refresh after changes)
    relay_host: str = "127.0.0.1"
    relay_port: int = 9000
    # Use the lighter relay refresh after each change. Set
    # NETAUDIO_GUI_RESTART_ON_CHANGE=1 to force a full daemon restart instead
    # (more reliable but costs ~seconds per action).
    restart_on_change: bool = False
    # Where named routing scenes (presets) are persisted.
    presets_path: str = "~/.config/netaudio-webgui/presets.json"
    # Where login users are stored (username -> scrypt hash; plaintext entries
    # are hashed on first load and written back).
    users_path: str = "~/.config/netaudio-webgui/users.json"
    # Where touch-dashboard zone config is persisted.
    zones_path: str = "~/.config/netaudio-webgui/zones.json"


def load_settings() -> Settings:
    return Settings(
        bind=os.environ.get("NETAUDIO_GUI_BIND", "0.0.0.0"),
        port=int(os.environ.get("NETAUDIO_GUI_PORT", "36342")),
        demo=_truthy(os.environ.get("NETAUDIO_GUI_DEMO")),
        netaudio_bin=os.environ.get("NETAUDIO_BIN", "netaudio"),
        discovery_timeout=float(os.environ.get("NETAUDIO_GUI_TIMEOUT", "2.0")),
        relay_host=os.environ.get("NETAUDIO_RELAY_HOST", "127.0.0.1"),
        relay_port=int(os.environ.get("NETAUDIO_RELAY_PORT", "9000")),
        restart_on_change=_truthy(os.environ.get("NETAUDIO_GUI_RESTART_ON_CHANGE", "0")),
        presets_path=os.environ.get("NETAUDIO_GUI_PRESETS")
        or os.path.expanduser("~/.config/netaudio-webgui/presets.json"),
        users_path=os.environ.get("NETAUDIO_GUI_USERS")
        or os.path.expanduser("~/.config/netaudio-webgui/users.json"),
        zones_path=os.environ.get("NETAUDIO_GUI_ZONES")
        or os.path.expanduser("~/.config/netaudio-webgui/zones.json"),
    )
