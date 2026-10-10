"""The live pipeline: one stream of typed events, handled by components that only know events.

A component subclasses `Component` and marks the methods for the events it cares about:

    class Summary(Component):
        @enrich(Lap)            # adds to a Lap before anyone acts on it
        def words(self, e: Lap): ...

        @on(CornerExit)         # acts on it: updates the shared state, emits more events
        def note(self, e: CornerExit): ...

`@on(Event)` (the base class) sees every event (the rule engine does). For each event, every
enricher runs, then every handler, in component order. Events emitted meanwhile are queued and
handled after it, first in, first out, so a replay makes the same decisions as the live session.

Components share state through `pipe.state` (`iagent.live.state.CoachState`) and otherwise talk
only through events. The pipeline knows no particular event but `Frame` and `FrameDone`: new
behaviour is a new event, component or rule, never a change here.

Other threads (the browser, the engineer) hand events in with `post`, which is thread-safe; they're
handled on the coach's thread at the next frame. `dispatch` handles an event at once, nested, for
the rare one that must come before the rest of the current event (a line crossing, before the
frame that starts the new lap is counted in it).
"""

import logging
import queue
from abc import ABC
from collections import defaultdict, deque
from typing import Callable, TypeVar

from iagent.live.events import EVENTS, Event, Frame, FrameDone
from iagent.live.state import CoachState
from iagent.telemetry.frames import Frame as TelemetryFrame

logger = logging.getLogger("iagent.live")

E = TypeVar("E", bound=Event)
Handler = Callable[[Event], None]


def on(*types: type[Event]):
    """Mark a component method as a handler of these events."""
    def mark(fn):
        fn._handles = (*getattr(fn, "_handles", ()), *types)
        return fn
    return mark


def enrich(*types: type[Event]):
    """Mark a component method as adding to these events before they're handled."""
    def mark(fn):
        fn._enriches = (*getattr(fn, "_enriches", ()), *types)
        return fn
    return mark


class Component(ABC):
    """Base of the pipeline's components. `ctx` is what every component may read (session, plan,
    corner map, reference lap, arbiter, ...), `settings` the coach's settings, `state` the shared
    state. Override `start` to set up (after every component exists)."""

    def __init__(self, pipe: "Pipeline"):
        self.pipe = pipe

    def start(self) -> None:
        pass

    @property
    def state(self) -> CoachState:
        return self.pipe.state

    @property
    def settings(self):
        return self.pipe.settings

    @property
    def ctx(self):
        return self.pipe.ctx

    def emit(self, event: E) -> E:
        return self.pipe.emit(event)


class Pipeline:
    def __init__(self, ctx, settings, components: list[type[Component]]):
        self.ctx = ctx
        self.settings = settings
        self.state = CoachState()
        self.channels: dict[str, float] = {}  # the latest frame's values
        self.now = 0.0
        self._queue: deque[Event] = deque()
        self._inbox: queue.Queue[Event] = queue.Queue()
        self._enrichers: dict[type, list[Handler]] = defaultdict(list)
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._listeners: dict[type, list[Handler]] = defaultdict(list)  # outside subscribers
        self.components: list[Component] = []
        for cls in components:
            self.add(cls(self))
        for c in self.components:
            c.start()

    def add(self, component: Component) -> Component:
        self.components.append(component)
        for attr in dir(type(component)):
            fn = getattr(type(component), attr, None)
            for kind, table in (("_enriches", self._enrichers), ("_handles", self._handlers)):
                for t in getattr(fn, kind, ()):
                    table[t].append(getattr(component, attr))
        return component

    def get(self, cls: type[Component]):
        """The component of class CLS (for tests and the facade)."""
        return next(c for c in self.components if isinstance(c, cls))

    def on(self, event: type[Event] | str, fn: Handler) -> None:
        """Subscribe from outside (the session log, the CLI, tests): called after the components.
        `Event` (or "*") is every event; a name works too."""
        cls = Event if event == "*" else EVENTS[event] if isinstance(event, str) else event
        self._listeners[cls].append(fn)

    # --- events --------------------------------------------------------------------------------

    def emit(self, event: E) -> E:
        event.at = self.now
        self._queue.append(event)
        return event

    def post(self, event: Event) -> None:
        """From any thread: handled on the coach's thread at the next frame."""
        self._inbox.put(event)

    def push(self, frame: TelemetryFrame) -> None:
        self.now = frame.session_time
        self.channels = dict(frame.values)
        self.run_inbox()
        self.emit(Frame(frame=frame))
        self.run()
        self.emit(FrameDone())
        self.run()

    def run_inbox(self) -> None:
        """Handle what other threads have posted (done at every frame)."""
        while True:
            try:
                event = self._inbox.get_nowait()
            except queue.Empty:
                break
            self.emit(event)
        self.run()

    def run(self) -> None:
        """Handle queued events until none are left."""
        while self._queue:
            self._handle(self._queue.popleft())

    def dispatch(self, event: E) -> E:
        """Handle an event now (nested in the current one); what it emits is queued as usual."""
        event.at = self.now
        self._handle(event)
        return event

    def _handle(self, e: Event) -> None:
        t = type(e)
        for fn in self._enrichers.get(t, ()):
            fn(e)
        for fn in (*self._handlers.get(t, ()), *self._handlers.get(Event, ())):
            fn(e)
        for fn in (*self._listeners.get(t, ()), *self._listeners.get(Event, ())):
            try:
                fn(e)
            except Exception:  # an outside listener never stops the coach
                logger.exception("Listener for %s failed", e.name)
