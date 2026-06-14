from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from netaudio_webgui.config import Settings, load_settings
from netaudio_webgui.netaudio_client import NetaudioClient, NetaudioError

STATIC_DIR = Path(__file__).parent / "static"


class SubscriptionBody(BaseModel):
    tx_device: str
    tx_number: int
    rx_device: str
    rx_number: int


class RemoveBody(BaseModel):
    rx_device: str
    rx_number: int


class NameBody(BaseModel):
    name: str


class ChannelNameBody(BaseModel):
    name: str
    type: str  # "tx" | "rx"


def _make_client(settings: Settings):
    if settings.demo:
        from netaudio_webgui.fixtures import DemoClient
        return DemoClient()
    return NetaudioClient(
        settings.netaudio_bin, settings.discovery_timeout,
        relay_host=settings.relay_host, relay_port=settings.relay_port,
        restart_on_change=settings.restart_on_change,
    )


def create_app(settings: Settings | None = None, client=None) -> FastAPI:
    settings = settings or load_settings()
    client = client or _make_client(settings)
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
        # Force a full refresh (daemon restart by default) for changes made
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

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
