"""The local web server behind `iagent ui`: a JSON API over the workspace, plus the built
React app (web/, built into iagent/ui/static/).

It runs on the driver's machine, so it only answers to a local Host name (unless `iagent ui`
was bound to the network on purpose), and every POST must carry an `X-Iagent` header. A web
page on another site can't send that header without a CORS preflight, which this server never
grants, so a site open in the browser can't start the coach or write to the workspace.
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from iagent.laps.watch import TelemetryWatcher, default_telemetry_dir, ingest_file
from iagent.references import garage61 as g61
from iagent.references import ghosts
from iagent.references import imports
from iagent.ui import review
from iagent.ui.coach import CoachRuns
from iagent.workspace import Workspace, WorkspaceError

STATIC_DIR = Path(__file__).parent / "static"
LOCAL_HOSTS = ["localhost", "127.0.0.1"]
CLIENT_HEADER = "x-iagent"
G61_CACHE_S = 60.0

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
    allowed_hosts: list[str] | None = None,
    token_file: Path | None = None,
) -> Starlette:
    coach = coach or CoachRuns(workspace)
    ghost_dir = (workspace / "reference" / "ghosts").resolve()
    g61_account: dict = {}  # Garage61's answer about the token, asked once
    # Garage61's laps per track/car for a minute: the library and the review screen both ask, and
    # Garage61 rate-limits.
    g61_found: dict[tuple[str, str], tuple[float, dict]] = {}

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
                hit = g61_found.get((track, car))
                if hit is None or time.monotonic() - hit[0] > G61_CACHE_S:
                    hit = g61_found[(track, car)] = (time.monotonic(), imports.find(ws, client, track, car))
                already = imports.imported(ws)  # imported since it was cached: still current
                laps = [{**row, "lap_id": already.get(row["garage61_id"])} for row in hit[1]["laps"]]
                return {"available": True, **hit[1], "laps": laps}
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

    def telemetry_status() -> dict:
        return watcher.status() if watcher else {
            "folder": str(default_telemetry_dir()), "found": default_telemetry_dir().is_dir(), "watching": False,
            "files_ingested": 0, "last": None, "error": None, "version": 0,
        }

    def has_token() -> bool:
        try:
            garage61().close()
            return True
        except WorkspaceError:
            return False

    async def system(request: Request) -> JSONResponse:
        """What this machine can do: is iRacing here (ghosts install), is its telemetry watched,
        is Garage61 set up, can the coach run."""
        lap_dir = lapfiles or ghosts.default_lapfiles()
        return JSONResponse({
            "platform": sys.platform,
            "lapfiles": str(lap_dir),
            "lapfiles_found": lap_dir.is_dir(),
            "telemetry": telemetry_status(),
            "garage61": {"token": has_token()},
            "coach": {"found": shutil.which(coach.claude) is not None, "command": coach.claude},
        })

    async def ingest(request: Request) -> JSONResponse:
        """One .ibt file as the request body (?name= its file name, which dates the session)."""
        name = request.query_params.get("name", "").replace("\\", "/").split("/")[-1]
        if not name.lower().endswith(".ibt"):
            return JSONResponse({"error": "Only iRacing telemetry files (.ibt) can be imported."}, status_code=400)
        (workspace / "cache").mkdir(parents=True, exist_ok=True)
        upload = Path(tempfile.mkdtemp(prefix="upload-", dir=workspace / "cache"))
        try:
            path = upload / name
            with path.open("wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
            try:
                out = await run_in_threadpool(ingest_file, workspace, path)
            except Exception as e:  # a damaged or foreign file: say so, keep serving
                return JSONResponse({"error": f"Couldn't read {name}: {e}"}, status_code=422)
            return JSONResponse({"file": name, **out})
        finally:
            shutil.rmtree(upload, ignore_errors=True)

    async def telemetry_folder(request: Request) -> JSONResponse:
        if watcher is None:
            return JSONResponse({"error": "Not watching a folder: `iagent ui` was started with --no-watch."},
                                status_code=409)
        folder = Path(str((await request.json()).get("folder") or "")).expanduser()
        if not str(folder).strip() or not folder.is_dir():
            return JSONResponse({"error": f"No such folder: {folder}"}, status_code=400)
        watcher.set_folder(folder)
        return JSONResponse(watcher.status())

    async def telemetry_rescan(request: Request) -> JSONResponse:
        if watcher is None:
            return JSONResponse({"error": "Not watching a folder: `iagent ui` was started with --no-watch."},
                                status_code=409)
        found = await run_in_threadpool(watcher.rescan)
        return JSONResponse({"ingested": found, "telemetry": watcher.status()})

    def account() -> dict:
        """Who the Garage61 token belongs to and their teams (asked once, it rarely changes)."""
        if not g61_account:
            try:
                client = garage61()
            except WorkspaceError as e:
                return {"connected": False, "reason": str(e)}
            try:
                me, teams = client.me(), client.teams()
            except g61.Garage61Error as e:
                return {"connected": False, "reason": str(e)}
            finally:
                client.close()
            g61_account.update({"connected": True, "user": me.get("slug"),
                                "teams": [(t.get("name") or t.get("slug") or "").strip() for t in teams]})
        return g61_account

    async def garage61_status(request: Request) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(account))

    async def garage61_token(request: Request) -> JSONResponse:
        token = str((await request.json()).get("token") or "").strip()
        if not token:
            return JSONResponse({"error": "token is required"}, status_code=400)
        g61.save_token(token, token_file)
        g61_account.clear()
        return JSONResponse(await run_in_threadpool(account))

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
        Route("/api/ingest", ingest, methods=["POST"]),
        Route("/api/telemetry/folder", telemetry_folder, methods=["POST"]),
        Route("/api/telemetry/rescan", telemetry_rescan, methods=["POST"]),
        Route("/api/garage61/status", garage61_status),
        Route("/api/garage61/token", garage61_token, methods=["POST"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/chat/stop", chat_stop, methods=["POST"]),
    ]
    if (static_dir / "index.html").exists():
        routes.append(Mount("/", StaticFiles(directory=static_dir, html=True)))
    else:
        routes.append(Route("/", not_built))
    middleware = [
        Middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or LOCAL_HOSTS),
        Middleware(RequireClientHeader),
    ]
    return Starlette(routes=routes, middleware=middleware)


class RequireClientHeader:
    """Refuse any request other than GET/HEAD without the X-Iagent header (see the module doc)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD"):
            if not any(k.decode("latin-1").lower() == CLIENT_HEADER for k, _ in scope["headers"]):
                response = JSONResponse({"error": "Missing X-Iagent header."}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
