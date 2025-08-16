import asyncio
import queue
import threading
from typing import Sequence

import irsdk

from iagent import utils
from iagent.events.detectors.base import DetectorBase
from iagent.events.detectors.lockup import LockUpDetector
from iagent.events.events import EventPriority

DEBUG = True


class EventListener:
    def __init__(self, fps: int = 20):
        self.low_priority_queue = queue.Queue()
        self.high_priority_queue = queue.Queue()

        self._ir = irsdk.IRSDK()
        self._intersample_time = float(1 / fps)
        self._detectors: Sequence[DetectorBase] = (
            LockUpDetector(),
        )

    async def listen(self, stop_event: threading.Event):
        if DEBUG:
            self._ir.startup(
                test_file=str(utils.get_misc_data_path() / "data.bin")
            )
        else:
            self._ir.startup()

        # TODO - check for interrupts
        while True:
            if stop_event.is_set():
                self._ir.shutdown()
                return
            self._ir.freeze_var_buffer_latest()
            self.detect_events()
            self._ir.unfreeze_var_buffer_latest()
            await asyncio.sleep(self._intersample_time)

    def detect_events(self):
        for detector in self._detectors:
            event = detector.detect(self._ir)
            if event is None:
                continue
            if event.priority == EventPriority.LOW:
                self.low_priority_queue.put(event)
            else:
                self.high_priority_queue.put(event)
        return None
