"""
A utility application to map the position data of a track manually (i.e. run the sim, drive the track
and map the entry and exit of each turn).
"""
import enum
import json
import logging
import tkinter as tk
from pathlib import Path
from typing import Optional

from collections import defaultdict
from irsdk import IRSDK

from iagent import utils

OUTPUT_DIR = Path(__file__).parent / "track-data"

logger = logging.getLogger(__name__)


class CornerPhase(enum.Flag):
    ENTRY = True
    EXIT = False


class TurnLabeller:
    def __init__(self, track: str):
        self._curr_turn = 1
        self._corner_phase: CornerPhase = CornerPhase.ENTRY
        self._label: Optional[tk.Label] = None
        self._data = {"turns": defaultdict(dict)}
        self._ir = IRSDK()
        self._track = track
        self._is_bound = False

    def bind(self, root: tk.Tk):
        root.bind("<Return>", self.mark_turn)
        root.bind("<Escape>", self.finish)
        self._label = tk.Label(root, text=self.get_label_text(), font=("Arial", 14))
        self._label.pack(pady=20)
        self._ir.startup()
        self._is_bound = True

    def get_label_text(self):
        return f"Current Turn: T{self._curr_turn} {self._corner_phase.name}"

    def mark_turn(self, _event: tk.Event):
        assert self._is_bound, "Please first `bind` to a Tkinter root."
        self._data["turns"][self._curr_turn][self._corner_phase.name] = {"LapDistPct": self._ir["LapDistPct"]}

        if self._corner_phase == CornerPhase.EXIT:
            self._curr_turn += 1
        # Alternate between entry and exit
        self._corner_phase = CornerPhase(not self._corner_phase)
        self._label.configure(text=self.get_label_text())

    def finish(self, _event: tk.Event):
        fpath = OUTPUT_DIR / f"{self._track}.json"
        logger.info(f"Writing turns data to {fpath}")
        with open(fpath, "w") as stream:
            stream.write(json.dumps(self._data, indent=4))
        exit(0)


def main() -> None:
    utils.setup_logger(__name__)
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    track = input("Track Name: ").strip().lower().replace(" ", "-")

    root = tk.Tk()
    root.title("Track Mapper")
    root.geometry("300x100")
    tlabel = TurnLabeller(track)
    tlabel.bind(root)

    logger.info("Press [ENTER] to map a turn. Press [ESC] once finished.")
    root.mainloop()


if __name__ == '__main__':
    main()
