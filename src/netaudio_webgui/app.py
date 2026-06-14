from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from netaudio_webgui.config import Settings, load_settings
from netaudio_webgui.netaudio_client import NetaudioClient, NetaudioError
from netaudio_webgui.presets import PresetStore

STATIC_DIR = Path(__file__).parent / "static"


class SubscriptionBody(BaseModel):
    tx_device: str
    tx_number: int
    rx_device: str
    rx_number: int


class RemoveBody(BaseModel):
    rx_device: str
    rx_number: int


class BulkSubscriptionBody(BaseModel):
    tx_device: str
    rx_device: str
    count: int = 0
    offset_tx: int = 0
    offset_rx: int = 0


class NameBody(BaseModel):
    name: str


class ChannelNameBody(BaseModel):
    name: str
    type: str  # "tx" | "rx"


def plan_apply(desired: list[dict], state: dict) -> tuple[list[dict], list[dict], int]:
    """Compute the diff to make the live routing EXACTLY match ``desired``.

    ``desired`` is a list of ``{rx_device, rx_channel, tx_device, tx_channel}``
    with channels given as display LABELS. Resolution uses ``state["devices"]``
    per-device label->number maps. Returns ``(add, remove, skipped)``:
      * add    — desired subs not currently present, as
                 ``{tx_device, tx_number, rx_device, rx_number}``.
      * remove — current subs not in desired, as ``{rx_device, rx_number}``.
      * skipped — desired subs whose device/channel can't be resolved.
    Identity is keyed on (rx_device, rx_channel, tx_device, tx_channel) labels;
    only one sub per RX channel is possible in Dante, so an add that changes the
    TX of an already-subscribed RX is simply an add (netaudio overwrites).
    """
    # Per-device label -> number maps for rx and tx channels.
    rx_numbers: dict[str, dict[str, int]] = {}
    tx_numbers: dict[str, dict[str, int]] = {}
    for device in state.get("devices", []):
        name = device.get("name", "")
        rx_numbers[name] = {c["label"]: c["number"] for c in device.get("rx_channels", [])}
        tx_numbers[name] = {c["label"]: c["number"] for c in device.get("tx_channels", [])}

    def key(sub: dict) -> tuple[str, str, str, str]:
        return (sub.get("rx_device", ""), sub.get("rx_channel", ""),
                sub.get("tx_device", ""), sub.get("tx_channel", ""))

    current = {key(s): s for s in state.get("subscriptions", [])}
    desired_keys: set[tuple[str, str, str, str]] = set()

    add: list[dict] = []
    skipped = 0
    for sub in desired:
        rx_device = sub.get("rx_device", "")
        tx_device = sub.get("tx_device", "")
        rx_number = rx_numbers.get(rx_device, {}).get(sub.get("rx_channel", ""))
        tx_number = tx_numbers.get(tx_device, {}).get(sub.get("tx_channel", ""))
        if rx_number is None or tx_number is None:
            skipped += 1
            continue
        k = key(sub)
        desired_keys.add(k)
        if k not in current:
            add.append({
                "tx_device": tx_device, "tx_number": tx_number,
                "rx_device": rx_device, "rx_number": rx_number,
            })

    remove: list[dict] = []
    for k, sub in current.items():
        if k in desired_keys:
            continue
        rx_device = sub.get("rx_device", "")
        rx_number = rx_numbers.get(rx_device, {}).get(sub.get("rx_channel", ""))
        if rx_number is None:
            continue  # unresolvable RX label — can't issue a removal
        remove.append({"rx_device": rx_device, "rx_number": rx_number})

    return add, remove, skipped


def _make_client(settings: Settings):
    if settings.demo:
        from netaudio_webgui.fixtures import DemoClient
        return DemoClient()
    return NetaudioClient(
        settings.netaudio_bin, settings.discovery_timeout,
        relay_host=settings.relay_host, relay_port=settings.relay_port,
        restart_on_change=settings.restart_on_change,
    )


def create_app(settings: Settings | None = None, client=None, store=None) -> FastAPI:
    settings = settings or load_settings()
    client = client or _make_client(settings)
    store = store or PresetStore(settings.presets_path)
    app = FastAPI(title="netaudio Web GUI")

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if not settings.token:
            return
        expected = f"Bearer {settings.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="missing or invalid token")

    auth = [Depends(require_token)]

    @app.exception_handler(NetaudioError)
    async def _netaudio_error_handler(_request, exc: NetaudioError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/api/state", dependencies=auth)
    def api_state():
        return client.get_state()

    @app.post("/api/rescan", dependencies=auth)
    def api_rescan():
        # Force a full refresh (relay rescan by default) for changes made
        # outside this GUI (Dante Controller, channel-count edits, device restarts).
        client.force_refresh()
        return {"ok": True}

    @app.post("/api/subscription", dependencies=auth)
    def api_add_subscription(body: SubscriptionBody):
        client.add_subscription(
            tx_device=body.tx_device, tx_number=body.tx_number,
            rx_device=body.rx_device, rx_number=body.rx_number,
        )
        return {"ok": True}

    @app.delete("/api/subscription", dependencies=auth)
    def api_remove_subscription(body: RemoveBody):
        client.remove_subscription(rx_device=body.rx_device, rx_number=body.rx_number)
        return {"ok": True}

    @app.post("/api/subscription/bulk", dependencies=auth)
    def api_add_bulk_subscription(body: BulkSubscriptionBody):
        client.add_bulk_subscription(
            tx_device=body.tx_device, rx_device=body.rx_device,
            count=body.count, offset_tx=body.offset_tx, offset_rx=body.offset_rx,
        )
        return {"ok": True}

    @app.put("/api/device/{host}/name", dependencies=auth)
    def api_device_name(host: str, body: NameBody):
        client.set_device_name(host, body.name)
        return {"ok": True}

    @app.put("/api/device/{host}/channel/{number}/name", dependencies=auth)
    def api_channel_name(host: str, number: int, body: ChannelNameBody):
        client.set_channel_name(host, number, body.name, body.type)
        return {"ok": True}

    @app.post("/api/device/{host}/identify", dependencies=auth)
    def api_identify(host: str):
        client.identify(host)
        return {"ok": True}

    @app.post("/api/device/{host}/reboot", dependencies=auth)
    def api_reboot(host: str):
        client.reboot(host)
        return {"ok": True}

    @app.get("/api/presets", dependencies=auth)
    def api_list_presets():
        return {"presets": store.list()}

    @app.post("/api/presets", dependencies=auth)
    def api_save_preset(body: NameBody):
        subs = [
            {"rx_device": s["rx_device"], "rx_channel": s["rx_channel"],
             "tx_device": s["tx_device"], "tx_channel": s["tx_channel"]}
            for s in client.get_state()["subscriptions"]
        ]
        try:
            store.save(body.name, subs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True, "count": len(subs)}

    @app.post("/api/presets/{name}/apply", dependencies=auth)
    def api_apply_preset(name: str):
        try:
            desired = store.get(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"preset {name!r} not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        add, remove, skipped = plan_apply(desired, client.get_state())
        added = 0
        for a in add:
            client.add_subscription(**a)
            added += 1
        removed = 0
        for r in remove:
            client.remove_subscription(**r)
            removed += 1
        return {"ok": True, "added": added, "removed": removed, "skipped": skipped}

    @app.delete("/api/presets/{name}", dependencies=auth)
    def api_delete_preset(name: str):
        try:
            store.delete(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"preset {name!r} not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
