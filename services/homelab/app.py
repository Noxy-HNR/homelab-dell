"""Authenticated LAN hub for the HP and Dell. Run with one worker."""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings, load_settings


class Login(BaseModel):
    token: str


def create_app(settings: Settings) -> FastAPI:
    if settings.role not in ("hp", "dell") or len(settings.token) < 32:
        raise ValueError("A valid role and a login token of at least 32 characters are required")
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Homelab", docs_url=None, redoc_url=None, openapi_url=None)
    sessions: dict[str, float] = {}
    failures: dict[str, list[float]] = {}
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def access(request: Request, call_next):
        if request.url.path.startswith("/api"):
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid request length"}, status_code=400)
            if length > 1024 * 1024:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
            # Browsers must send same-origin JSON. CLI/MCP use Bearer without Origin.
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Use the server's own page"}, status_code=403)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                if request.headers.get("x-homelab-request") != "1" and not request.headers.get("authorization"):
                    return JSONResponse({"detail": "Missing request header"}, status_code=403)
            if request.url.path != "/api/login":
                bearer = request.headers.get("authorization", "")
                token_ok = bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], settings.token)
                cookie = request.cookies.get("homelab_session", "")
                session_ok = sessions.get(cookie, 0) > time.time()
                if not (token_ok or session_ok):
                    return JSONResponse({"detail": "Sign in with this server's login token"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health():
        return {"ok": True, "role": settings.role}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        now = time.time()
        peer = request.client.host if request.client else "unknown"
        failures[peer] = [t for t in failures.get(peer, []) if now - t < 60]
        if len(failures[peer]) >= 10:
            raise HTTPException(429, "Wait one minute before trying again")
        if not hmac.compare_digest(body.token, settings.token):
            failures[peer].append(now)
            raise HTTPException(401, "Incorrect login token")
        failures.pop(peer, None)
        for sid, expiry in list(sessions.items()):
            if expiry <= now:
                sessions.pop(sid, None)
        if len(sessions) >= 128:
            sessions.pop(next(iter(sessions)))
        sid = secrets.token_urlsafe(32)
        sessions[sid] = now + 8 * 3600
        response.set_cookie("homelab_session", sid, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=8 * 3600)
        return {"ok": True, "role": settings.role}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get("homelab_session", ""), None)
        response.delete_cookie("homelab_session")
        return {"ok": True}

    @app.get("/api/status")
    def status():
        return {"role": settings.role, "hostname": socket.gethostname(),
                "hp_url": settings.hp_url, "dell_url": settings.dell_url,
                "features": (["models", "downloads"] if settings.role == "hp" else ["projects", "jobs", "search", "mcp"]) + ["metrics", "backups"]}

    @app.get("/api/steam")
    def steam():
        if settings.role != "hp":
            raise HTTPException(404, "Steam ownership lives on the HP")
        controller = Path("/usr/local/sbin/game-lun-controller.py")
        if not controller.is_file():
            return {"available": False, "message": "Complete the Steam setup after preparing both Windows PCs."}
        try:
            result = subprocess.run(["sudo", "-n", "/usr/bin/python3", str(controller), "status"],
                                    capture_output=True, text=True, timeout=5, check=False)
            if result.returncode:
                return {"available": False, "message": "Controller status is unavailable; check it from the HP console."}
            return {"available": True, "status": json.loads(result.stdout)}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"available": False, "message": "Controller status is unavailable."}

    from .operations import create_router as operations_router
    app.include_router(operations_router(settings), prefix="/api")
    if settings.role == "hp":
        from .models import create_router as models_router
        app.include_router(models_router(settings), prefix="/api")
    else:
        from .workbench import create_router as workbench_router
        app.include_router(workbench_router(settings), prefix="/api")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    guide = Path(__file__).parent.parent / "guide"
    if not guide.is_dir():
        guide = Path(__file__).resolve().parents[2] / "docs"
    if guide.is_dir():
        app.mount("/guide", StaticFiles(directory=guide, html=True), name="guide")
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/homelab/apps.json")
    args = parser.parse_args()
    raw = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    settings = load_settings(args.config)
    import uvicorn
    uvicorn.run(create_app(settings), host=raw.get("listen_host", "127.0.0.1"),
                port=int(raw.get("port", 8088 if settings.role == "hp" else 8090)),
                workers=1, proxy_headers=False)


if __name__ == "__main__":
    main()
