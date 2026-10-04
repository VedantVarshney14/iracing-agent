"""The local web server behind `iagent ui`: a JSON API over the workspace, plus the built
React app (web/, built into iagent/ui/static/)."""

from pathlib import Path
from typing import Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from iagent.ui import review
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


def create_app(workspace: Path, static_dir: Path = STATIC_DIR) -> Starlette:
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

    async def not_built(request: Request) -> HTMLResponse:
        return HTMLResponse(_NOT_BUILT)

    routes = [
        Route("/api/tracks", tracks),
        Route("/api/laps", laps),
        Route("/api/review", review_lap),
    ]
    if (static_dir / "index.html").exists():
        routes.append(Mount("/", StaticFiles(directory=static_dir, html=True)))
    else:
        routes.append(Route("/", not_built))
    return Starlette(routes=routes)
