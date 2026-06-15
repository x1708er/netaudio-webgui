from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _empty() -> dict:
    return {"master": {"buttons": [], "off": False}, "zones": []}


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


class ZoneStore:
    """Persist the touch-dashboard zone config to a single JSON file.

    Shape: ``{"master": {"buttons": [str], "off": bool},
              "zones": [{"name", "rx": [{"device","channel"}], "buttons": [str], "off": bool}]}``.
    A missing or corrupt file loads as the empty config. Writes are atomic
    (temp file + ``os.replace``) and mode 0600."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return _empty()
        try:
            return self.normalize(data)
        except ValueError:
            return _empty()

    def save(self, config: dict) -> None:
        normalized = self.normalize(config)  # raises ValueError on malformed input
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(normalized, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".zones-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def normalize(data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("config must be an object")
        master_in = data.get("master") or {}
        if not isinstance(master_in, dict):
            raise ValueError("master must be an object")
        master = {"buttons": _str_list(master_in.get("buttons")),
                  "off": bool(master_in.get("off", False))}

        zones_in = data.get("zones")
        if zones_in is None:
            zones_in = []
        if not isinstance(zones_in, list):
            raise ValueError("zones must be a list")

        zones: list[dict] = []
        seen: set[str] = set()
        for z in zones_in:
            if not isinstance(z, dict):
                raise ValueError("each zone must be an object")
            name = (z.get("name") or "").strip()
            if not name:
                raise ValueError("zone name must not be empty")
            if name in seen:
                raise ValueError(f"duplicate zone name: {name}")
            seen.add(name)
            rx: list[dict] = []
            for r in (z.get("rx") or []):
                if not isinstance(r, dict):
                    raise ValueError("rx entry must be an object")
                device = (r.get("device") or "").strip()
                channel = (r.get("channel") or "").strip()
                if not device or not channel:
                    raise ValueError("rx entry needs device and channel")
                rx.append({"device": device, "channel": channel})
            zones.append({"name": name, "rx": rx,
                          "buttons": _str_list(z.get("buttons")),
                          "off": bool(z.get("off", False))})
        return {"master": master, "zones": zones}
