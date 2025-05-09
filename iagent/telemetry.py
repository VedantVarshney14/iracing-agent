import logging
import time
from threading import Event

import irsdk
import sqlalchemy
from sqlalchemy.orm import Session

from iagent.db.tables import Telemetry


COLLECTION_RATE = 60

logger = logging.getLogger(__name__)


class TelemetryCollectionClient:
    def __init__(self, engine: sqlalchemy.Engine, ir: irsdk.IRSDK):
        self.engine = engine
        self.ir = ir

        self._telem_cols =  [
            col.name for col in Telemetry.__table__.columns
            if col.name[0].isupper()
        ]

    def insert_frame(self):
        self.ir.freeze_var_buffer_latest()
        with Session(self.engine) as session:
            record = Telemetry(
                **{k: self.ir[k] for k in self._telem_cols}
            )
            session.add(record)
            session.commit()


    def collect(self, stop: Event):
        logger.info("Collecting telemetry")
        while not stop.is_set():
            self.insert_frame()
            time.sleep(1 / COLLECTION_RATE)
        logger.info("Stopped collecting telemetry")
