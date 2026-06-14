from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class PresetStore:
    """Persist named routing scenes to a single JSON file.

    File format: ``{"presets": {"<name>": [ {sub}, ... ]}}`` where each sub is
    ``{"rx_device","rx_channel","tx_device","tx_channel"}`` (channel = display
    LABEL). A missing or corrupt file is treated as empty on read. Writes are
    atomic (temp file in the same dir, then ``os.replace``).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("preset name must not be empty")
        return cleaned

    def _read(self) -> dict[str, list[dict]]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        presets = data.get("presets")
        if not isinstance(presets, dict):
            return {}
        return presets

    def _write(self, presets: dict[str, list[dict]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"presets": presets}, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".presets-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def list(self) -> list[str]:
        return sorted(self._read().keys())

    def save(self, name: str, subscriptions: list[dict]) -> None:
        name = self._clean_name(name)
        presets = self._read()
        presets[name] = [
            {
                "rx_device": s.get("rx_device", ""),
                "rx_channel": s.get("rx_channel", ""),
                "tx_device": s.get("tx_device", ""),
                "tx_channel": s.get("tx_channel", ""),
            }
            for s in subscriptions
        ]
        self._write(presets)

    def get(self, name: str) -> list[dict]:
        name = self._clean_name(name)
        presets = self._read()
        if name not in presets:
            raise KeyError(name)
        return presets[name]

    def delete(self, name: str) -> None:
        name = self._clean_name(name)
        presets = self._read()
        if name not in presets:
            raise KeyError(name)
        del presets[name]
        self._write(presets)
