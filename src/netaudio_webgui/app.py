from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from netaudio_webgui.auth import SessionStore, UserStore
from netaudio_webgui.config import Settings, load_settings
from netaudio_webgui.netaudio_client import NetaudioClient, NetaudioError
from netaudio_webgui.presets import PresetStore
from netaudio_webgui.zones import ZoneStore

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


class ImportBody(BaseModel):
    subscriptions: list[dict]


class ChannelNameBody(BaseModel):
    name: str
    type: str  # "tx" | "rx"


class ConfigValueBody(BaseModel):
    # bool before int/float so JSON true/false isn't coerced to 1/0; float covers
    # the latency value (positive number), str covers aes67/preferred-leader "on"/"off".
    value: bool | int | float | str


class ChannelGainBody(BaseModel):
    level: int
    type: str  # "tx" | "rx"


class LoginBody(BaseModel):
    username: str
    password: str


class ZoneConfigBody(BaseModel):
    master: dict | None = None
    zones: list[dict] | None = None


def _coerce_bool(value) -> bool:
    """Accept a real bool or the strings on/off/true/false (any case)."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"on", "true", "1", "yes"}:
        return True
    if text in {"off", "false", "0", "no"}:
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def plan_apply(desired: list[dict], state: dict,
               scope: set[tuple[str, str]] | None = None) -> tuple[list[dict], list[dict], int]:
    """Compute the diff to make the live routing EXACTLY match ``desired``.

    ``desired`` is a list of ``{rx_device, rx_channel, tx_device, tx_channel}``
    with channels given as display LABELS. Resolution uses ``state["devices"]``
    per-device label->number maps. Returns ``(add, remove, skipped)``:
      * add    — desired subs not currently present, as
                 ``{tx_device, tx_number, rx_device, rx_number}``.
      * remove — current subs not in desired, as ``{rx_device, rx_number}``.
      * skipped — desired subs whose device/channel can't be resolved.
      * scope — optional set of (rx_device, rx_channel) LABEL pairs. When given,
                removals are limited to current subs whose RX is in scope; subs
                outside the scope are never touched (used for zone-scoped apply).
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
    # RX channels the desired routing subscribes (any TX). A Dante RX channel
    # holds at most one subscription, so an add to such a channel OVERWRITES
    # whatever was there — issuing a remove for it would nuke the just-added sub.
    desired_rx: set[tuple[str, int]] = set()
    for sub in desired:
        rx_device = sub.get("rx_device", "")
        tx_device = sub.get("tx_device", "")
        rx_number = rx_numbers.get(rx_device, {}).get(sub.get("rx_channel", ""))
        tx_number = tx_numbers.get(tx_device, {}).get(sub.get("tx_channel", ""))
        if rx_number is None or tx_number is None:
            skipped += 1
            continue
        desired_rx.add((rx_device, rx_number))
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
        rx_label = sub.get("rx_channel", "")
        if scope is not None and (rx_device, rx_label) not in scope:
            continue  # out of zone scope — leave untouched
        rx_number = rx_numbers.get(rx_device, {}).get(rx_label)
        if rx_number is None:
            continue  # unresolvable RX label — can't issue a removal
        if (rx_device, rx_number) in desired_rx:
            continue  # re-pointed channel: the desired add overwrites it, no remove needed
        remove.append({"rx_device": rx_device, "rx_number": rx_number})

    return add, remove, skipped


def apply_desired(client, desired: list[dict],
                  scope: set[tuple[str, str]] | None = None) -> dict:
    """Make the live routing EXACTLY match ``desired`` (label-keyed subs).

    Runs ``plan_apply`` against the current state, performs the add/remove
    client calls, and returns ``{"added", "removed", "skipped"}``. Shared by
    the preset-apply endpoint and the matrix import endpoint.
    """
    add, remove, skipped = plan_apply(desired, client.get_state(), scope=scope)
    added = 0
    for a in add:
        client.add_subscription(**a)
        added += 1
    removed = 0
    for r in remove:
        client.remove_subscription(**r)
        removed += 1
    return {"added": added, "removed": removed, "skipped": skipped}


def _zone_scope(zone: dict) -> set[tuple[str, str]]:
    return {(r["device"], r["channel"]) for r in zone.get("rx", [])}


def _scene_slice(subs: list[dict], scope: set[tuple[str, str]]) -> set[tuple[str, str, str, str]]:
    """The (rx_device, rx_channel, tx_device, tx_channel) tuples of ``subs`` whose
    RX falls within ``scope``."""
    return {
        (s.get("rx_device", ""), s.get("rx_channel", ""),
         s.get("tx_device", ""), s.get("tx_channel", ""))
        for s in subs
        if (s.get("rx_device", ""), s.get("rx_channel", "")) in scope
    }


def _active_button(buttons: list[str], current: set, store, scope: set,
                   has_off: bool) -> str | None:
    """Which button (scene name) matches ``current`` (live routing sliced to
    ``scope``)? Prefer a non-empty scene match; else "off" when empty; else None."""
    for name in buttons:
        try:
            sliced = _scene_slice(store.get(name), scope)
        except (KeyError, ValueError):
            continue
        if sliced and sliced == current:
            return name
    if not current and has_off:
        return "off"
    return None


def _make_client(settings: Settings):
    if settings.demo:
        from netaudio_webgui.fixtures import DemoClient
        return DemoClient()
    return NetaudioClient(
        settings.netaudio_bin, settings.discovery_timeout,
        relay_host=settings.relay_host, relay_port=settings.relay_port,
        restart_on_change=settings.restart_on_change,
    )


def create_app(settings: Settings | None = None, client=None, store=None,
               users: UserStore | None = None, zones: ZoneStore | None = None) -> FastAPI:
    settings = settings or load_settings()
    client = client or _make_client(settings)
    store = store or PresetStore(settings.presets_path)
    zones = zones or ZoneStore(settings.zones_path)
    if users is None:
        if settings.demo:
            users = UserStore.from_plaintext({"demo": "demo"})
        else:
            users = UserStore.load(settings.users_path)
    if not users.usernames():
        raise RuntimeError(
            f"no users configured — create {settings.users_path} with "
            '{"<name>": "<password>"} entries (plaintext is hashed on first start)'
        )
    sessions = SessionStore()
    app = FastAPI(title="netaudio Web GUI")

    SESSION_COOKIE = "netaudio_session"

    def require_session(request: Request) -> None:
        if sessions.get(request.cookies.get(SESSION_COOKIE)) is None:
            raise HTTPException(status_code=401, detail="not authenticated")

    auth = [Depends(require_session)]

    @app.post("/api/login")
    def api_login(body: LoginBody, response: Response):
        if not users.verify(body.username, body.password):
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = sessions.create(body.username)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, samesite="lax",
            max_age=30 * 24 * 3600, path="/",
        )
        return {"username": body.username}

    @app.post("/api/logout")
    def api_logout(request: Request, response: Response):
        sessions.delete(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me", dependencies=auth)
    def api_me(request: Request):
        session = sessions.get(request.cookies.get(SESSION_COOKIE))
        return {"username": session["username"]}

    # In-memory session audit log of mutating requests (bounded ring buffer).
    audit_log: deque[dict] = deque(maxlen=200)

    @app.middleware("http")
    async def _audit_middleware(request: Request, call_next):
        response = await call_next(request)
        if request.method in {"POST", "PUT", "DELETE"} and request.url.path.startswith("/api/"):
            audit_log.append({
                "ts": time.time(), "method": request.method,
                "path": request.url.path, "status": response.status_code,
            })
        return response

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

    @app.put("/api/device/{host}/config/{key}", dependencies=auth)
    def api_device_config(host: str, key: str, body: ConfigValueBody):
        # Validation (enum/range and bool coercion) raises ValueError -> HTTP 400.
        try:
            if key == "sample-rate":
                client.set_sample_rate(host, body.value)
            elif key == "encoding":
                client.set_encoding(host, body.value)
            elif key == "latency":
                client.set_latency(host, body.value)
            elif key == "aes67":
                client.set_aes67(host, _coerce_bool(body.value))
            elif key == "preferred-leader":
                client.set_preferred_leader(host, _coerce_bool(body.value))
            else:
                raise HTTPException(status_code=404, detail=f"unknown config key: {key!r}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.put("/api/device/{host}/channel/{number}/gain", dependencies=auth)
    def api_channel_gain(host: str, number: int, body: ChannelGainBody):
        try:
            client.set_channel_gain(host, number, body.level, body.type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
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
        return {"ok": True, **apply_desired(client, desired)}

    @app.post("/api/subscriptions/import", dependencies=auth)
    def api_import_subscriptions(body: ImportBody):
        return {"ok": True, **apply_desired(client, body.subscriptions)}

    @app.delete("/api/presets/{name}", dependencies=auth)
    def api_delete_preset(name: str):
        try:
            store.delete(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"preset {name!r} not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.get("/api/log", dependencies=auth)
    def api_log():
        return {"log": list(reversed(audit_log))}

    def _find_zone(name: str) -> dict:
        for z in zones.load()["zones"]:
            if z["name"] == name:
                return z
        raise HTTPException(status_code=404, detail=f"zone {name!r} not found")

    def _scene_for_scope(scene: str, scope: set[tuple[str, str]]) -> list[dict]:
        try:
            subs = store.get(scene)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"scene {scene!r} not found")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return [s for s in subs if (s.get("rx_device", ""), s.get("rx_channel", "")) in scope]

    @app.get("/api/zones", dependencies=auth)
    def api_zones_get():
        return zones.load()

    @app.put("/api/zones", dependencies=auth)
    def api_zones_put(body: ZoneConfigBody):
        """Replace the WHOLE zone config (not a partial merge); omitted master or
        zones reset to their empty default."""
        try:
            zones.save(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"ok": True}

    @app.post("/api/zones/apply/{scene}", dependencies=auth)
    def api_zones_master_apply(scene: str):
        scope: set[tuple[str, str]] = set()
        for z in zones.load()["zones"]:
            scope |= _zone_scope(z)
        desired = _scene_for_scope(scene, scope)
        return {"ok": True, **apply_desired(client, desired, scope=scope)}

    @app.post("/api/zones/off", dependencies=auth)
    def api_zones_master_off():
        scope: set[tuple[str, str]] = set()
        for z in zones.load()["zones"]:
            scope |= _zone_scope(z)
        return {"ok": True, **apply_desired(client, [], scope=scope)}

    @app.post("/api/zones/{zone}/apply/{scene}", dependencies=auth)
    def api_zone_apply(zone: str, scene: str):
        scope = _zone_scope(_find_zone(zone))
        desired = _scene_for_scope(scene, scope)
        return {"ok": True, **apply_desired(client, desired, scope=scope)}

    @app.post("/api/zones/{zone}/off", dependencies=auth)
    def api_zone_off(zone: str):
        scope = _zone_scope(_find_zone(zone))
        return {"ok": True, **apply_desired(client, [], scope=scope)}

    @app.get("/api/zones/state", dependencies=auth)
    def api_zones_state():
        config = zones.load()
        subs = client.get_state().get("subscriptions", [])
        result: dict = {"zones": {}, "master": None}
        master_scope: set[tuple[str, str]] = set()
        for z in config["zones"]:
            scope = _zone_scope(z)
            master_scope |= scope
            current = _scene_slice(subs, scope)
            result["zones"][z["name"]] = _active_button(
                z.get("buttons", []), current, store, scope, z.get("off", False))
        master = config.get("master", {})
        current = _scene_slice(subs, master_scope)
        result["master"] = _active_button(
            master.get("buttons", []), current, store, master_scope, master.get("off", False))
        return result

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


# Build the app lazily on attribute access. Importing this module (e.g. tests,
# which call create_app() directly with injected users) then needs no configured
# users.json. Running it for real — `uvicorn netaudio_webgui.app:app` — accesses
# `app`, which builds it and fails fast with a clear RuntimeError if no users are
# configured.
_app_instance = None


def __getattr__(name: str):
    if name == "app":
        global _app_instance
        if _app_instance is None:
            _app_instance = create_app()
        return _app_instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
