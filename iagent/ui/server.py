"""The local web server behind `iagent ui`: a JSON API over the workspace, plus the built
React app (web/, built into iagent/ui/static/)."""

import sys
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from iagent.laps.watch import TelemetryWatcher, default_telemetry_dir
from iagent.references import garage61 as g61
from iagent.references import ghosts
from iagent.references import imports
from iagent.ui import review
from iagent.ui.coach import CoachRuns
from iagent.workspace import Workspace, WorkspaceError

STATIC_DIR = Path(__file__).parent / "static"

_NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>iRacing Coach</title>
<body style="font-family: system-ui; background: #0D1013; color: #E8ECEF; padding: 40px">
<h1>The UI hasn't been built yet</h1>
<p>From the repository root:</p>
<pre>npm --prefix web install
npm --prefix web run build</pre>
<p>Then reload this page. The API is running: try <a style="color: #AFC6DE" href="/api/tracks">/api/tracks</a>.</p>
</body>"""


def create_app(
    workspace: Path,
    static_dir: Path = STATIC_DIR,
    coach: CoachRuns | None = None,
    garage61: Callable[[], g61.Garage61Client] = imports.client_from_env,
    watcher: TelemetryWatcher | None = None,
    lapfiles: Path | None = None,
) -> Starlette:
    coach = coach or CoachRuns(workspace)
    ghost_dir = (workspace / "reference" / "ghosts").resolve()

    async def call(fn: Callable[[Workspace], object]) -> JSONResponse:
        # Each request opens its own workspace: SQLite connections can't cross threads.
        def run():
            ws = Workspace(workspace)
            try:
                return fn(ws)
            finally:
                ws.close()

        try:
            return JSONResponse(await run_in_threadpool(run))
        except WorkspaceError as e:
            return JSONResponse({"error": str(e)}, status_code=404)

    async def tracks(request: Request) -> JSONResponse:
        return await call(review.tracks)

    async def laps(request: Request) -> JSONResponse:
        track, car = request.query_params.get("track"), request.query_params.get("car")
        if not track or not car:
            return JSONResponse({"error": "track and car are required"}, status_code=400)
        return await call(lambda ws: review.laps(ws, track, car))

    async def review_lap(request: Request) -> JSONResponse:
        lap = request.query_params.get("lap")
        if not lap:
            return JSONResponse({"error": "lap is required"}, status_code=400)
        return await call(lambda ws: review.review(ws, lap, request.query_params.get("ref") or None))

    async def garage61_laps(request: Request) -> JSONResponse:
        track, car = request.query_params.get("track"), request.query_params.get("car")
        if not track or not car:
            return JSONResponse({"error": "track and car are required"}, status_code=400)

        def find(ws: Workspace) -> dict:
            # Garage61 is optional: say why it's unavailable instead of failing the screen.
            try:
                client = garage61()
            except WorkspaceError as e:
                return {"available": False, "reason": str(e), "laps": []}
            try:
                return {"available": True, **imports.find(ws, client, track, car)}
            except (WorkspaceError, g61.Garage61Error) as e:
                return {"available": False, "reason": str(e), "laps": []}
            finally:
                client.close()

        return await call(find)

    async def garage61_import(request: Request) -> JSONResponse:
        gid = (await request.json()).get("garage61_id")
        if not gid:
            return JSONResponse({"error": "garage61_id is required"}, status_code=400)

        def run_import(ws: Workspace) -> dict:
            if lap_id := imports.imported(ws).get(gid):
                return {"lap_id": lap_id, "garage61_id": gid}
            client = garage61()
            try:
                return imports.import_lap(ws, client, gid)
            except g61.Garage61Error as e:
                raise WorkspaceError(f"Garage61: {e}") from e
            finally:
                client.close()

        return await call(run_import)

    async def system(request: Request) -> JSONResponse:
        """What this machine can do: is iRacing here (ghosts install), is its telemetry watched."""
        lap_dir = lapfiles or ghosts.default_lapfiles()
        telemetry = watcher.status() if watcher else {
            "folder": str(default_telemetry_dir()), "found": default_telemetry_dir().is_dir(), "watching": False,
            "files_ingested": 0, "last": None, "error": None, "version": 0,
        }
        return JSONResponse({"platform": sys.platform, "lapfiles": str(lap_dir), "lapfiles_found": lap_dir.is_dir(),
                             "telemetry": telemetry})

    async def garage61_ghost(request: Request) -> JSONResponse:
        body = await request.json()
        gid = body.get("garage61_id")
        if not gid:
            return JSONResponse({"error": "garage61_id is required"}, status_code=400)

        def run_ghost(ws: Workspace) -> dict:
            client = garage61()
            try:
                out = imports.ghost(ws, client, gid, install=bool(body.get("install")), lapfiles=lapfiles)
            except g61.Garage61Error as e:
                raise WorkspaceError(f"Garage61: {e}") from e
            finally:
                client.close()
            out["download"] = "/api/ghost-file?name=" + Path(out["saved"]).resolve().relative_to(ghost_dir).as_posix()
            return out

        return await call(run_ghost)

    async def ghost_file(request: Request) -> Response:
        path = (ghost_dir / request.query_params.get("name", "")).resolve()
        if not path.is_file() or ghost_dir not in path.parents:
            return JSONResponse({"error": "No such ghost file."}, status_code=404)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    async def chat(request: Request) -> StreamingResponse | JSONResponse:
        body = await request.json()
        message = (body.get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)
        events = coach.run(message, body.get("context") or {}, body.get("session_id") or None)
        return StreamingResponse(events, media_type="application/x-ndjson")

    async def chat_stop(request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse({"stopped": coach.stop(body.get("run_id", ""))})

    async def not_built(request: Request) -> HTMLResponse:
        return HTMLResponse(_NOT_BUILT)

    routes = [
        Route("/api/tracks", tracks),
        Route("/api/laps", laps),
        Route("/api/review", review_lap),
        Route("/api/garage61/laps", garage61_laps),
        Route("/api/garage61/import", garage61_import, methods=["POST"]),
        Route("/api/garage61/ghost", garage61_ghost, methods=["POST"]),
        Route("/api/ghost-file", ghost_file),
        Route("/api/system", system),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/chat/stop", chat_stop, methods=["POST"]),
    ]
    if (static_dir / "index.html").exists():
        routes.append(Mount("/", StaticFiles(directory=static_dir, html=True)))
    else:
        routes.append(Route("/", not_built))
    return Starlette(routes=routes)
