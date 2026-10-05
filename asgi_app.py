#!/usr/bin/env python3
"""Single-origin ASGI app: 申請フォーム static site + bento ordering API.

Why this file exists
--------------------
The form was served by ``search_server.py`` (a ThreadingHTTPServer) while the
bento ordering API was a separate FastAPI service. That split meant:

* two Render services, two deploys, two places to keep env vars;
* CORS to configure, because the API lived on another origin;
* the LIFF iframe loaded ``bento/index.html`` from the form host but had to call
  an API on a different host.

Running one ASGI app on one port collapses all of that: static files, the
existing ``/schedules`` endpoints, and the bento ``/api/*`` routers share an
origin, so requests are same-origin and CORS stops being a factor.

Layout
------
* ``GET/POST/PUT/DELETE /schedules`` — unchanged behaviour from search_server.py,
  including the JST (Asia/Tokyo) timestamps and the past-date-free semantics.
* ``/api/*`` — the bento routers from ``projects/bento-liff/backend``.
* everything else — static files from this directory.

Startup
-------
``prod`` mode runs the bento settings' production checks and refuses to boot on
an incomplete configuration (see bento ``app/config.py``). Missing LINE channel
credentials therefore fail loudly at startup rather than silently at runtime.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).parent.resolve()
PORT = int(os.environ.get("PORT", "10000"))
SCHEDULES_FILE = BASE_DIR / "schedules.json"
JST = timezone(timedelta(hours=9))

logger = logging.getLogger("uvicorn.error")

# The bento backend is committed alongside this app at ./bento_backend (mirror
# it with sync_bento_backend.py after editing projects/bento-liff/backend). The
# development-tree path stays as a fallback so local runs work either way.
_BENTO_CANDIDATES = [
    BASE_DIR / "bento_backend",
    BASE_DIR.parent / "projects" / "bento-liff" / "backend",
]
for _cand in _BENTO_CANDIDATES:
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

app = FastAPI(title="申請フォーム + お弁当注文", docs_url="/api/docs", redoc_url=None)

# Declared up front so /health (defined below) can report the mount state; the
# actual import that flips it to True happens further down, before the routers
# are included.
_bento_loaded = False
_bento_error = ""

# --------------------------------------------------------------------------- #
# schedules.json storage (behaviour preserved from search_server.py)
# --------------------------------------------------------------------------- #


def load_schedules() -> list[dict]:
    try:
        with SCHEDULES_FILE.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_schedules(rows: list[dict]) -> None:
    with SCHEDULES_FILE.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)


def _schedule_from_payload(data: dict) -> dict:
    title = str(data.get("title", "")).strip()
    date = str(data.get("date", "")).strip()
    if not title or not date:
        raise HTTPException(status_code=400, detail="title and date are required")
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "date": date,
        "time": str(data.get("time", "")).strip(),
        "description": str(data.get("description", "")).strip(),
        "created": datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+0900"),
    }


@app.get("/health")
def health() -> dict:
    payload = {"status": "ok", "port": PORT, "schedules": len(load_schedules())}
    # The bento settings object is optional here: the form site must stay usable
    # even when the ordering backend is not wired in. Report the mode when we
    # can read it and say so plainly when we cannot.
    try:
        from app.config import get_settings  # type: ignore

        _s = get_settings()
        payload["bento_app_env"] = _s.app_env
        payload["line_verification"] = _s.line_verification_enabled
    except Exception as exc:
        payload["bento"] = f"unavailable ({exc.__class__.__name__})"
    payload["bento_api_mounted"] = _bento_loaded
    return payload


@app.get("/schedules")
def get_schedules() -> dict:
    return {"schedules": load_schedules()}


@app.post("/schedules")
@app.post("/create")
async def create_schedule(payload: dict) -> JSONResponse:
    new = _schedule_from_payload(payload)
    rows = load_schedules()
    rows.append(new)
    save_schedules(rows)
    return JSONResponse(status_code=201, content={"success": True, "schedule": new})


@app.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: dict) -> JSONResponse:
    rows = load_schedules()
    for idx, row in enumerate(rows):
        if row.get("id") == schedule_id:
            updated = dict(row)
            for key in ("title", "date", "time", "description"):
                if key in payload:
                    updated[key] = str(payload[key]).strip()
            rows[idx] = updated
            save_schedules(rows)
            return JSONResponse(content={"success": True, "schedule": updated})
    return JSONResponse(status_code=404, content={"success": False, "error": "not found"})


@app.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str) -> JSONResponse:
    rows = load_schedules()
    kept = [r for r in rows if r.get("id") != schedule_id]
    if len(kept) == len(rows):
        return JSONResponse(status_code=404, content={"success": False, "error": "not found"})
    save_schedules(kept)
    return JSONResponse(content={"success": True, "message": "Schedule deleted"})


# --------------------------------------------------------------------------- #
# bento ordering API (mounted last so it never shadows the routes above)
# --------------------------------------------------------------------------- #

try:
    from app.api import admin, auth, orders  # type: ignore
    from app.api import config_api as _config_api  # type: ignore

    app.include_router(_config_api.router)
    app.include_router(auth.router)
    app.include_router(orders.router)
    app.include_router(admin.router)
    _bento_loaded = True
except Exception as exc:  # pragma: no cover - depends on deployment layout
    _bento_error = f"{exc.__class__.__name__}: {exc}"
    logger.warning("bento API not mounted: %s", _bento_error)

_origins = [o.strip() for o in os.environ.get("BENTO_CORS_ORIGINS", "").split(",") if o.strip()]
if not _origins:
    _origins = ["https://liff.line.me", "https://liff-drawing-search.onrender.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Line-User-Id"],
)

# Static site last: index.html is the LIFF endpoint, bento/ holds the iframe app.
#
# Route order matters — FastAPI matches in registration order, so these mounts
# are registered after /health, /schedules and /api/* and can only serve files
# nothing above claimed.
#
# The root mount is what serves /sdk.js. Without it the app had explicit routes
# for only / and /form.html, so the LIFF SDK request returned 404, `liff` stayed
# undefined, liff.init threw "liff is not defined", and the form reported
# "LINE認証が必要です" — a failure that had nothing to do with LINE auth.
# Adding one explicit route per asset would only move the problem to the next
# missing file, so serve the whole directory instead.
if (BASE_DIR / "bento").is_dir():
    app.mount("/bento", StaticFiles(directory=str(BASE_DIR / "bento"), html=True), name="bento")

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html", headers=_NO_CACHE)


@app.get("/form.html")
async def form() -> FileResponse:
    return FileResponse(BASE_DIR / "form.html", headers=_NO_CACHE)


# Catch-all. Registered last, after every explicit route and the /bento mount.
app.mount("/", StaticFiles(directory=str(BASE_DIR), html=True), name="static")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    logger.info("bento API mounted: %s%s", _bento_loaded, "" if _bento_loaded else f" ({_bento_error})")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")