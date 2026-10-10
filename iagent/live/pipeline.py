"""The live pipeline: one stream of events, handled by components that only know events.

A component is a class with methods named after the events it cares about:

- `enrich_<event>(e)` adds fields to an event before anyone acts on it (the corner tracker adds
  the feedback text to `corner_exit`, say);
- `on_<event>(e)` acts on it: updates shared state, emits further events;
- `on_any(e)` sees every event (the rule engine, the session log).

For each event, every enricher runs (in component order), then every handler. Events emitted
while handling one are queued and handled after it, first in, first out, so the order is
deterministic and replays make the same decisions as the live session.

Components share state through `pipe.state` (named values defined with `define_state`) and talk
only through events. The pipeline itself knows no event but `frame`: new behaviour is a new
component, event or rule, never a change here.

Other threads (the browser, the narrator) hand events in with `post`, which is thread-safe;
they're handled on the coach's thread at the next frame. `frame_done` comes after everything a
frame caused (speech ticks then). `dispatch` handles an event at once, nested, for the rare one
that must come before the rest of the current event (a line crossing, before the frame that
starts the new lap is counted in it).
"""

import logging
import queue
from collections import defaultdict, deque
from typing import Any, Callable

from iagent.live.events import EVENTS, Event
from iagent.telemetry.frames import Frame

logger = logging.getLogger("iagent.live")

Handler = Callable[[Event], None]


class Component:
    """Base for pipeline components. `pipe` gives the context (session, plan, corner map, ...),
    the settings, the shared state and `emit`."""

    def __init__(self, pipe: "Pipeline"):
        self.pipe = pipe

    @property
    def state(self) -> dict[str, Any]:
        return self.pipe.state

    @property
    def settings(self):
        return self.pipe.settings

    @property
    def ctx(self):
        return self.pipe.ctx

    def emit(self, type: str, **fields) -> Event:
        return self.pipe.emit(type, **fields)


class Pipeline:
    def __init__(self, ctx, settings, components: list[type[Component]]):
        self.ctx = ctx
        self.settings = settings
        self.state: dict[str, Any] = {}
        self.channels: dict[str, float] = {}  # the latest frame's values
        self.now = 0.0
        self._queue: deque[Event] = deque()
        self._inbox: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._enrich: dict[str, list[Handler]] = defaultdict(list)
        self._handle: dict[str, list[Handler]] = defaultdict(list)
        self._any: list[Handler] = []
        self._listeners: dict[str, list[Handler]] = defaultdict(list)  # outside subscribers
        self.components: list[Component] = []
        for cls in components:
            self.add(cls(self))
        for c in self.components:
            if hasattr(c, "start"):
                c.start()

    def add(self, component: Component) -> Component:
        self.components.append(component)
        for name in dir(component):
            if name.startswith("enrich_"):
                self._subscribe(self._enrich, name[len("enrich_"):], getattr(component, name), component)
            elif name == "on_any":
                self._any.append(component.on_any)
            elif name.startswith("on_"):
                self._subscribe(self._handle, name[len("on_"):], getattr(component, name), component)
        return component

    def _subscribe(self, table: dict, event: str, fn: Handler, component: Component) -> None:
        if event not in EVENTS:
            raise ValueError(f"{type(component).__name__}.{fn.__name__}: no event {event!r} is defined.")
        table[event].append(fn)

    def get(self, cls: type) -> Any:
        """The component of class CLS (for tests and the facade)."""
        return next(c for c in self.components if isinstance(c, cls))

    def on(self, event: str, fn: Handler) -> None:
        """Subscribe from outside (the session log, the CLI, tests): called after the components.
        "*" is every event."""
        if event != "*" and event not in EVENTS:
            raise ValueError(f"No event {event!r} is defined.")
        self._listeners[event].append(fn)

    # --- events --------------------------------------------------------------------------------

    def emit(self, type: str, **fields) -> Event:
        if type not in EVENTS:
            raise ValueError(f"No event {type!r} is defined: define it with define_event.")
        e = Event(type, self.now, fields)
        self._queue.append(e)
        return e

    def post(self, type: str, **fields) -> None:
        """From any thread: handled on the coach's thread at the next frame."""
        if type not in EVENTS:
            raise ValueError(f"No event {type!r} is defined.")
        self._inbox.put((type, fields))

    def push(self, frame: Frame) -> None:
        self.now = frame.session_time
        self.channels = dict(frame.values)
        self.run_inbox()
        self.emit("frame", frame=frame)
        self.run()
        self.emit("frame_done")
        self.run()

    def run_inbox(self) -> None:
        """Handle what other threads have posted (done at every frame)."""
        while True:
            try:
                type, fields = self._inbox.get_nowait()
            except queue.Empty:
                break
            self.emit(type, **fields)
        self.run()

    def run(self) -> None:
        """Handle queued events until none are left."""
        while self._queue:
            self._handle_one(self._queue.popleft())

    def dispatch(self, type: str, **fields) -> Event:
        """Handle an event now (nested in the current one); what it emits is queued as usual."""
        if type not in EVENTS:
            raise ValueError(f"No event {type!r} is defined.")
        e = Event(type, self.now, fields)
        self._handle_one(e)
        return e

    def _handle_one(self, e: Event) -> None:
        for fn in self._enrich.get(e.type, ()):
            fn(e)
        for fn in self._handle.get(e.type, ()):
            fn(e)
        for fn in self._any:
            fn(e)
        for fn in [*self._listeners.get(e.type, []), *self._listeners.get("*", [])]:
            try:
                fn(e)
            except Exception:  # an outside listener never stops the coach
                logger.exception("Listener for %s failed", e.type)
