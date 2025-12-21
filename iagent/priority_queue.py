import heapq
import queue
import threading
from typing import Tuple, Any


class ThreadPriorityQueue:
    """A minimal priority queue safe to use from multiple threads.

    Items are tuples (priority, counter, payload) to keep stable ordering.
    Lower numeric priority values are served first.
    """

    def __init__(self):
        self._heap: list[Tuple[int, int, Any]] = []
        self._lock = threading.Lock()
        self._counter = 0

    def put(self, priority: int, item: Any):
        with self._lock:
            self._counter += 1
            heapq.heappush(self._heap, (priority, self._counter, item))

    def get_nowait(self) -> Any:
        with self._lock:
            if not self._heap:
                raise queue.Empty
            _, _, item = heapq.heappop(self._heap)
            return item

    def empty(self) -> bool:
        with self._lock:
            return not bool(self._heap)
