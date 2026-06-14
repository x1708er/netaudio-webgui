from __future__ import annotations

import copy

from netaudio_webgui.netaudio_client import NetaudioError

_DEMO_DEVICES = [
    {
        "name": "Inferno", "ipv4": "192.168.178.51", "server_name": "Inferno-demo",
        "online": True, "model": "Inferno (virtual)", "sample_rate": 48000, "clock_role": "leader",
        "tx_channels": [{"number": 1, "name": "L", "label": "L"},
                        {"number": 2, "name": "R", "label": "R"}],
        "rx_channels": [{"number": 1, "name": "01", "label": "01"},
                        {"number": 2, "name": "02", "label": "02"}],
    },
    {
        "name": "A32", "ipv4": "192.168.178.50", "server_name": "A32-demo",
        "online": True, "model": "AVIO A32", "sample_rate": 48000, "clock_role": "follower",
        "tx_channels": [{"number": 1, "name": "Mic1", "label": "Mic1"}],
        "rx_channels": [{"number": 1, "name": "01", "label": "01"},
                        {"number": 2, "name": "02", "label": "02"}],
    },
]

_DEMO_SUBSCRIPTIONS = [
    {"rx_device": "A32", "rx_channel": "01", "tx_device": "Inferno", "tx_channel": "L",
     "state": "connected", "label": "Connected"},
]


class DemoClient:
    """In-memory stand-in for NetaudioClient (NETAUDIO_GUI_DEMO=1)."""

    def __init__(self):
        self._devices = copy.deepcopy(_DEMO_DEVICES)
        self._subscriptions = copy.deepcopy(_DEMO_SUBSCRIPTIONS)

    def refresh(self, device: str | None = None) -> None:
        # No daemon in demo mode; state is always live in-memory.
        pass

    def rescan(self) -> None:
        pass

    def restart_daemon(self) -> None:
        pass

    def force_refresh(self) -> None:
        pass

    def get_state(self) -> dict:
        devices = sorted(copy.deepcopy(self._devices), key=lambda d: d["name"].lower())
        leader = next((d["name"] for d in devices if d["clock_role"].lower() == "leader"), None)
        return {
            "devices": devices,
            "subscriptions": copy.deepcopy(self._subscriptions),
            "leader": leader,
        }

    def _find_device(self, name: str) -> dict:
        for device in self._devices:
            if device["name"] == name:
                return device
        raise NetaudioError(f"device {name!r} not found")

    def _find_device_by_host(self, host: str) -> dict:
        for device in self._devices:
            if device["ipv4"] == host:
                return device
        raise NetaudioError(f"device with ip {host!r} not found")

    def _channel_label(self, device: dict, kind: str, number: int) -> str:
        for channel in device[kind]:
            if channel["number"] == number:
                return channel["label"]
        raise NetaudioError(f"channel {number} not found")

    def add_subscription(self, tx_device: str, tx_number: int, rx_device: str, rx_number: int) -> None:
        tx = self._find_device(tx_device)
        rx = self._find_device(rx_device)
        tx_label = self._channel_label(tx, "tx_channels", tx_number)
        rx_label = self._channel_label(rx, "rx_channels", rx_number)
        self._subscriptions = [
            s for s in self._subscriptions
            if not (s["rx_device"] == rx_device and s["rx_channel"] == rx_label)
        ]
        self._subscriptions.append({
            "rx_device": rx_device, "rx_channel": rx_label,
            "tx_device": tx_device, "tx_channel": tx_label,
            "state": "connected", "label": "Connected",
        })

    def remove_subscription(self, rx_device: str, rx_number: int) -> None:
        rx = self._find_device(rx_device)
        rx_label = self._channel_label(rx, "rx_channels", rx_number)
        self._subscriptions = [
            s for s in self._subscriptions
            if not (s["rx_device"] == rx_device and s["rx_channel"] == rx_label)
        ]

    def set_device_name(self, host: str, new_name: str) -> None:
        self._find_device_by_host(host)["name"] = new_name

    def set_channel_name(self, host: str, number: int, new_name: str, channel_type: str) -> None:
        if channel_type not in ("tx", "rx"):
            raise NetaudioError(f"invalid channel type: {channel_type!r}")
        device = self._find_device_by_host(host)
        kind = "tx_channels" if channel_type == "tx" else "rx_channels"
        for channel in device[kind]:
            if channel["number"] == number:
                old_label = channel["label"]
                channel["label"] = new_name
                # Keep existing subscriptions pointing at this channel consistent.
                side = "tx" if channel_type == "tx" else "rx"
                for sub in self._subscriptions:
                    if sub[f"{side}_device"] == device["name"] and sub[f"{side}_channel"] == old_label:
                        sub[f"{side}_channel"] = new_name
                return
        raise NetaudioError(f"channel {number} not found")

    def identify(self, host: str) -> None:
        self._find_device_by_host(host)

    def reboot(self, host: str) -> None:
        self._find_device_by_host(host)
