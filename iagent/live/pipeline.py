"""The live pipeline: one stream of typed events, acted on by event handlers that only know events.

An event handler subclasses `EventHandler` and marks the methods for the events it cares about:

    class Summary(EventHandler):
        @enrich(Lap)            # adds to a Lap before anyone acts on it
        def words(self, e: Lap): ...

        @on(CornerExit)         # acts on it: updates the shared state, emits more events
        def note(self, e: CornerExit): ...

Only events defined in `iagent.live.events` go through: `@on` and `@enrich` take `Event` classes,
and only named (registered) events can be emitted. A method marked for a class also gets its
subclasses: `@on(AtTheLine)` sees every `Crossing` and `Lap`, and `@on(Event)` sees every event
(the rule engine does). For each event, every enricher runs, then every handler, in the order the
event handlers were added. Events emitted meanwhile are queued and handled after it, first in,
first out, so a replay makes the same decisions as the live session.

Event handlers share state through `pipe.state` (`iagent.live.state.CoachState`) and otherwise
talk only through events. The pipeline knows no particular event but `Frame` and `FrameDone`: new
behaviour is a new event, event handler or rule, never a change here.

Other threads (the browser, the engineer) hand events in with `post`, which is thread-safe; they're
handled on the coach's thread at the next frame. `dispatch` handles an event at once, nested, for
the rare one that must come before the rest of the current event (a line crossing, before the
frame that starts the new lap is counted in it).
"""

import logging
import queue
from collections import defaultdict, deque
from typing import Callable, TypeVar

from iagent.live.events import EVENTS, Event, Frame, FrameDone
from iagent.live.state import CoachState
from iagent.telemetry.frames import Frame as TelemetryFrame

logger = logging.getLogger("iagent.live")

E = TypeVar("E", bound=Event)
Callback = Callable[[Event], None]


def _event_classes(types: tuple) -> tuple[type[Event], ...]:
    """TYPES, if every one is an `Event` class; a TypeError otherwise."""
    for t in types:
        if not (isinstance(t, type) and issubclass(t, Event)):
            raise TypeError(f"{t!r} is not an event: mark methods for Event classes (iagent.live.events).")
    return types


def on(*types: type[Event]):
    """Mark an event handler's method as acting on these events (and their subclasses)."""
    _event_classes(types)
    def mark(fn):
        fn._handles = (*getattr(fn, "_handles", ()), *types)
        return fn
    return mark


def enrich(*types: type[Event]):
    """Mark an event handler's method as adding to these events (and their subclasses) before
    they're acted on."""
    _event_classes(types)
    def mark(fn):
        fn._enriches = (*getattr(fn, "_enriches", ()), *types)
        return fn
    return mark


class EventHandler:
    """Base of what the pipeline runs: a class whose methods act on events, marked with `@on` or
    `@enrich`. `ctx` is what every handler may read (session, plan, corner map, reference lap,
    arbiter, ...), `settings` the coach's settings, `state` the shared state. Override `start` to
    set up (after every handler exists)."""

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
    """Runs events through the event handlers in order, one frame at a time, on the coach's thread;
    other threads hand events in through its inbox."""

    def __init__(self, ctx, settings, handlers: list[type[EventHandler]]):
        self.ctx = ctx
        self.settings = settings
        self.state = CoachState()
        self.channels: dict[str, float] = {}  # the latest frame's values
        self.now = 0.0
        self._queue: deque[Event] = deque()
        self._inbox: queue.Queue[Event] = queue.Queue()
        # Per event class: (order added, callback). `_routes` caches what each emitted class gets.
        self._enrichers: dict[type, list[tuple[int, Callback]]] = defaultdict(list)
        self._handlers: dict[type, list[tuple[int, Callback]]] = defaultdict(list)
        self._listeners: dict[type, list[tuple[int, Callback]]] = defaultdict(list)  # outside subscribers
        self._routes: dict[type, tuple[list[Callback], ...]] = {}
        self._added = 0
        self.handlers: list[EventHandler] = []
        for cls in handlers:
            self.add(cls(self))
        for h in self.handlers:
            h.start()

    def add(self, handler: EventHandler) -> EventHandler:
        """Register HANDLER's marked methods. TypeError if it isn't an EventHandler or marks no
        methods."""
        if not isinstance(handler, EventHandler):
            raise TypeError(f"{handler!r} is not an EventHandler.")
        marked = 0
        for attr in dir(type(handler)):
            fn = getattr(type(handler), attr, None)
            for kind, table in (("_enriches", self._enrichers), ("_handles", self._handlers)):
                for t in getattr(fn, kind, ()):
                    table[t].append(self._in_order(getattr(handler, attr)))
                    marked += 1
        if not marked:
            raise TypeError(f"{type(handler).__name__} acts on no events: mark its methods with @on or @enrich.")
        self.handlers.append(handler)
        return handler

    def get(self, cls: type[EventHandler]):
        """The event handler of class CLS (for tests and the facade)."""
        return next(h for h in self.handlers if isinstance(h, cls))

    def on(self, event: type[Event] | str, fn: Callback) -> None:
        """Subscribe from outside (the session log, the CLI, tests): called after the handlers.
        `Event` (or "*") is every event; a name works too."""
        if event == "*":
            cls = Event
        elif isinstance(event, str):
            if event not in EVENTS:
                raise KeyError(f"No event is called {event!r}.")
            cls = EVENTS[event]
        else:
            (cls,) = _event_classes((event,))
        self._listeners[cls].append(self._in_order(fn))

    def _in_order(self, fn: Callback) -> tuple[int, Callback]:
        """FN with the next registration number (handlers run in that order); forgets cached routes."""
        self._added += 1
        self._routes.clear()
        return self._added, fn

    def _route(self, t: type[Event]) -> tuple[list[Callback], ...]:
        """(enrichers, handlers, listeners) for an event of class T: everything marked for T or a
        class above it, in the order it was added."""
        if t not in self._routes:
            classes = [c for c in t.__mro__ if isinstance(c, type) and issubclass(c, Event)]
            self._routes[t] = tuple(
                [fn for _, fn in sorted((entry for c in classes for entry in table.get(c, ())), key=lambda x: x[0])]
                for table in (self._enrichers, self._handlers, self._listeners)
            )
        return self._routes[t]

    # --- events --------------------------------------------------------------------------------

    def emit(self, event: E) -> E:
        """Queue EVENT, stamped with the current session time; it's handled after the event being
        handled now."""
        _check_defined(event)
        event.at = self.now
        self._queue.append(event)
        return event

    def post(self, event: Event) -> None:
        """From any thread: handled on the coach's thread at the next frame."""
        _check_defined(event)
        self._inbox.put(event)

    def push(self, frame: TelemetryFrame) -> None:
        """One telemetry frame: handle what other threads posted, then the `Frame` and everything it
        leads to, then `FrameDone` (speech)."""
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
        _check_defined(event)
        event.at = self.now
        self._handle(event)
        return event

    def _handle(self, e: Event) -> None:
        """Run E through its enrichers, then its handlers, then outside listeners (whose errors are
        logged, never raised)."""
        enrichers, handlers, listeners = self._route(type(e))
        for fn in enrichers:
            fn(e)
        for fn in handlers:
            fn(e)
        for fn in listeners:
            try:
                fn(e)
            except Exception:  # an outside listener never stops the coach
                logger.exception("Listener for %s failed", e.name)


def _check_defined(event: Event) -> None:
    """Only named events (registered in `EVENTS`) go through, never a base like `AtTheLine`."""
    if not isinstance(event, Event) or EVENTS.get(type(event).__dict__.get("name", "")) is not type(event):
        raise TypeError(f"{event!r} is not a defined event: its class needs a `name` (iagent.live.events).")
