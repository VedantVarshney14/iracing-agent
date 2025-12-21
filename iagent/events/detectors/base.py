from typing import Optional
import abc

import irsdk

from iagent.events.events import EventStamp

class DetectorBase(abc.ABC):

    @abc.abstractmethod
    def detect(self, ir: irsdk.IRSDK) -> Optional[EventStamp]:
        raise NotImplementedError