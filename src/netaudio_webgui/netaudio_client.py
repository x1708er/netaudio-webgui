from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request


def build_state_argv(netaudio_bin: str, timeout: float) -> list[str]:
    return [netaudio_bin, "--timeout", str(timeout), "--output", "json", "device", "list"]


def build_add_subscription_argv(
    netaudio_bin: str, tx_device: str, tx_number: int, rx_device: str, rx_number: int
) -> list[str]:
    return [
        netaudio_bin, "subscription", "add",
        "--tx", f"{tx_number}@{tx_device}",
        "--rx", f"{rx_number}@{rx_device}",
    ]


def build_remove_subscription_argv(netaudio_bin: str, rx_device: str, rx_number: int) -> list[str]:
    return [netaudio_bin, "subscription", "remove", "--rx", f"{rx_number}@{rx_device}"]


def build_device_name_argv(netaudio_bin: str, host: str, new_name: str) -> list[str]:
    return [netaudio_bin, "--host", host, "device", "name", new_name]


def build_channel_name_argv(
    netaudio_bin: str, host: str, number: int, new_name: str, channel_type: str
) -> list[str]:
    return [
        netaudio_bin, "--host", host,
        "channel", "name", str(number), new_name, "--type", channel_type,
    ]


def build_identify_argv(netaudio_bin: str, host: str) -> list[str]:
    return [netaudio_bin, "--host", host, "device", "identify"]


def build_reboot_argv(netaudio_bin: str, host: str) -> list[str]:
    return [netaudio_bin, "--host", host, "device", "reboot"]


def _channels(channels_json: dict) -> list[dict]:
    result = []
    for number_str, info in channels_json.items():
        name = info.get("name") or ""
        result.append({
            "number": int(number_str),
            "name": name,
            "label": info.get("friendly_name") or name,
        })
    result.sort(key=lambda c: c["number"])
    return result


# Well-known Dante subscription status codes that netaudio v0.2.5 ships unable to
# decode (its status catalog is empty except code 0). 9 = "dynamic" (unicast flow
# established), 10 = "static" (multicast flow established) — both mean connected.
# Used only as a fallback when netaudio itself returns state "unknown", so a future
# netaudio that decodes these wins.
_KNOWN_SUBSCRIPTION_STATUS = {
    9: ("connected", "Connected (dynamic)"),
    10: ("connected", "Connected (static)"),
}


def parse_state(device_list_json: dict) -> dict:
    devices = []
    subscriptions = []
    leader = None
    # Per-device maps from a channel reference (raw name OR label) to its display label.
    tx_labels: dict[str, dict[str, str]] = {}
    rx_labels: dict[str, dict[str, str]] = {}

    raw_entries = list(device_list_json.values())

    # Pass 1: parse devices and build channel-reference -> label resolution maps.
    for entry in raw_entries:
        channels = entry.get("channels") or {}
        device = {
            "name": entry.get("name") or "",
            "ipv4": entry.get("ipv4") or "",
            "server_name": entry.get("server_name") or "",
            "online": bool(entry.get("online", True)),
            "model": entry.get("dante_model") or entry.get("model") or "",
            "sample_rate": entry.get("sample_rate"),
            "clock_role": entry.get("ptp_v1_role") or entry.get("clock_role") or "",
            "tx_channels": _channels(channels.get("transmitters") or {}),
            "rx_channels": _channels(channels.get("receivers") or {}),
        }
        devices.append(device)

        if device["clock_role"].lower() == "leader":
            leader = device["name"]

        tx_map: dict[str, str] = {}
        for channel in device["tx_channels"]:
            tx_map[channel["name"]] = channel["label"]
            tx_map[channel["label"]] = channel["label"]
        rx_map: dict[str, str] = {}
        for channel in device["rx_channels"]:
            rx_map[channel["name"]] = channel["label"]
            rx_map[channel["label"]] = channel["label"]
        tx_labels[device["name"]] = tx_map
        rx_labels[device["name"]] = rx_map

    # Pass 2: aggregate subscriptions, resolving channel refs to the display labels
    # the matrix uses so active routes light up even when friendly_name != name.
    for entry in raw_entries:
        for sub in entry.get("subscriptions") or []:
            status = sub.get("status") or {}
            rx_device = sub.get("rx_device") or ""
            tx_device = sub.get("tx_device") or ""
            rx_channel = sub.get("rx_channel") or ""
            tx_channel = sub.get("tx_channel") or ""
            rx_channel = rx_labels.get(rx_device, {}).get(rx_channel, rx_channel)
            tx_channel = tx_labels.get(tx_device, {}).get(tx_channel, tx_channel)
            state = status.get("state") or "unknown"
            label = status.get("label") or ""
            # If netaudio couldn't decode the status, fall back to the well-known
            # Dante connected codes so established subscriptions aren't shown amber.
            if state in ("unknown", "") and status.get("code") in _KNOWN_SUBSCRIPTION_STATUS:
                state, label = _KNOWN_SUBSCRIPTION_STATUS[status["code"]]
            subscriptions.append({
                "rx_device": rx_device,
                "rx_channel": rx_channel,
                "tx_device": tx_device,
                "tx_channel": tx_channel,
                "state": state,
                "label": label,
            })

    devices.sort(key=lambda d: d["name"].lower())
    return {"devices": devices, "subscriptions": subscriptions, "leader": leader}


class NetaudioError(Exception):
    """A netaudio CLI invocation failed."""


class NetaudioClient:
    def __init__(self, netaudio_bin: str, discovery_timeout: float,
                 relay_host: str = "127.0.0.1", relay_port: int = 9000,
                 restart_on_change: bool = False):
        self.netaudio_bin = netaudio_bin
        self.discovery_timeout = discovery_timeout
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.restart_on_change = restart_on_change

    def restart_daemon(self) -> None:
        """Restart the netaudio daemon and block until it has re-discovered
        devices. This is the only fully reliable refresh: the daemon marks
        devices offline after ~15s without a heartbeat and then won't re-query
        them, so a plain relay refresh can miss changes. Costs a few seconds."""
        try:
            subprocess.run([self.netaudio_bin, "daemon", "restart"],
                           capture_output=True, text=True, timeout=25.0)
        except Exception:
            return
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                status = subprocess.run([self.netaudio_bin, "daemon", "status"],
                                        capture_output=True, text=True, timeout=5.0)
            except Exception:
                break
            match = re.search(r"(\d+)\s+device\(s\) cached", status.stdout or "")
            if match and int(match.group(1)) > 0:
                return
            time.sleep(0.25)

    def _after_change(self, device: str | None = None) -> None:
        """Make the daemon reflect a change we just made out-of-band."""
        if self.restart_on_change:
            self.restart_daemon()
        elif device:
            self.refresh(device)
        else:
            self.rescan()

    def force_refresh(self) -> None:
        """Explicit, full refresh (the 'Neu einlesen' button)."""
        if self.restart_on_change:
            self.restart_daemon()
        else:
            self.rescan()

    def refresh(self, device: str | None = None) -> None:
        """Ask the netaudio daemon to re-query device state so its cache reflects
        changes made out-of-band (our CLI mutations talk to devices directly,
        not via the daemon). Pass a device NAME: a per-device refresh re-queries
        that device even when the daemon has marked it offline, whereas a blanket
        refresh skips offline devices. Best-effort: failures are ignored (a
        one-shot scan is the fallback when no daemon is running)."""
        url = f"http://{self.relay_host}:{self.relay_port}/refresh"
        data = b""
        headers = {}
        if device:
            data = json.dumps({"device": device}).encode()
            headers["Content-Type"] = "application/json"
        try:
            request = urllib.request.Request(url, data=data, method="POST", headers=headers)
            with urllib.request.urlopen(request, timeout=self.discovery_timeout + 15.0):
                pass
        except Exception:
            pass

    def rescan(self) -> None:
        """Refresh every known device by name. Per-device refreshes bypass the
        daemon's offline gate (devices drop to 'offline' after ~15s without a
        Dante heartbeat), so this reliably picks up out-of-band changes."""
        devices = self.get_state().get("devices", [])
        if not devices:
            self.refresh()
            return
        for device in devices:
            name = device.get("name")
            if name:
                self.refresh(device=name)

    def _run(self, argv: list[str], timeout: float = 30.0):
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as exc:
            raise NetaudioError(
                f"netaudio binary not found: {self.netaudio_bin!r}. "
                "Set NETAUDIO_BIN or ensure ~/.local/bin is on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise NetaudioError(f"netaudio timed out: {' '.join(argv)}") from exc

    def _run_checked(self, argv: list[str]) -> str:
        result = self._run(argv)
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "unknown error").strip()
            raise NetaudioError(message)
        return result.stdout

    def get_state(self) -> dict:
        argv = build_state_argv(self.netaudio_bin, self.discovery_timeout)
        result = self._run(argv, timeout=self.discovery_timeout + 15.0)
        if result.returncode != 0:
            raise NetaudioError((result.stderr or "discovery failed").strip())
        stdout = (result.stdout or "").strip()
        if not stdout:
            return {"devices": [], "subscriptions": [], "leader": None}
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # json mode with no devices may emit a plain message
            return {"devices": [], "subscriptions": [], "leader": None}
        if not isinstance(data, dict):
            return {"devices": [], "subscriptions": [], "leader": None}
        return parse_state(data)

    def add_subscription(self, tx_device: str, tx_number: int, rx_device: str, rx_number: int) -> None:
        self._run_checked(
            build_add_subscription_argv(self.netaudio_bin, tx_device, tx_number, rx_device, rx_number)
        )
        self._after_change(rx_device)

    def remove_subscription(self, rx_device: str, rx_number: int) -> None:
        self._run_checked(build_remove_subscription_argv(self.netaudio_bin, rx_device, rx_number))
        self._after_change(rx_device)

    def set_device_name(self, host: str, new_name: str) -> None:
        self._run_checked(build_device_name_argv(self.netaudio_bin, host, new_name))
        self._after_change()

    def set_channel_name(self, host: str, number: int, new_name: str, channel_type: str) -> None:
        if channel_type not in ("tx", "rx"):
            raise NetaudioError(f"invalid channel type: {channel_type!r}")
        self._run_checked(build_channel_name_argv(self.netaudio_bin, host, number, new_name, channel_type))
        self._after_change()

    def identify(self, host: str) -> None:
        self._run_checked(build_identify_argv(self.netaudio_bin, host))

    def reboot(self, host: str) -> None:
        self._run_checked(build_reboot_argv(self.netaudio_bin, host))
