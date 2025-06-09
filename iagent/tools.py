import json
import logging
from typing import Any, Optional, Union

import irsdk
import numpy as np

from iagent import utils

logger = logging.getLogger(__name__)



class IRacingTools:

    def __init__(self, ir: irsdk.IRSDK):
        self._ir = ir
        self._telemetry_defs = {
            "keys": [],
            "definitions": [],
            "map": {}
        }
        with open(utils.get_data_path() / "vars.json", "rb") as f:
            self._telemetry_defs["map"] = json.load(f)

        for k, v in self._telemetry_defs["map"].items():
            self._telemetry_defs["keys"].append(k)
            self._telemetry_defs["definitions"].append(v)

        logger.info("Setting up sentence transformer.")
        # Heavy import and only used once - load at runtime
        from sentence_transformers import SentenceTransformer

        self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
        self._telemetry_def_embed = self._embed(
            self._telemetry_defs["definitions"],
            normalize=True
        )

    @property
    def is_alive(self) -> bool:
        return self._ir.is_initialized and self._ir.is_connected

    def _embed(self, texts: Union[str, list[str]], normalize: bool = True):
        return self._embedder.encode(texts, normalize_embeddings=normalize)

    def get_current_telemetry_data(self, key: str) -> Optional[Any]:
        """
        Get current iRacing telemetry data.

        Parameters
        ----------
        key : str
            Telemetry item key.

        Notes
        -----
        Common telemetry keys:
        - Speed - GPS vehicle speed, m/s
        - Throttle - 0=off throttle to 1=full throttle, %
        - Brake - 0=brake released to 1=max pedal force, %
        - Gear - -1=reverse  0=neutral  1..n=current gear
        - LapBestLapTime - Players best lap time, s
        - LapCurrentLapTime - Estimate of players current lap time as shown in F3 box, s

        Examples
        --------
        >>> get_current_telemetry_data("Speed")
        >>> get_current_telemetry_data("AirDensity")

        Raises
        ------
        RuntimeError if an invalid telemetry key is encountered.

        Returns
        -------
        Optional[Any]
            Current telemetry data for specified key.
        """

        if not self.is_alive:
            raise RuntimeError("iRacing is not initialised. Please ensure the sim is running.")

        data = self._ir[key]
        if data is None:
            raise RuntimeError(f"No such telemetry key '{key}'. Please ensure that the key is valid.")
        return data

    def telemetry_key_embedding_lookup(self, text: str) -> list[dict[str, str]]:
        """
        Perform an embedding look-up to find the telemetry keys/headings most closely associated with the
        provided text. This utility is useful for finding telemetry keys which can then be
        subsequently used to retrieve telemetry data using `get_current_telemetry_data`.

        Parameters
        ----------
        text : str
            Look-up text

        Returns
        -------
        list[dict[str, str]]
            Closest matching telemetry keys - list of dictionaries.where the keys are the telemetry keys and
            the values are the corresponding telemetry values.
        """
        k = 5
        text_embed = self._embed(text, normalize=True)
        top_similarities_indices = np.argpartition(
            self._telemetry_def_embed.dot(text_embed),
            -k
        )[-k:]
        return [
            {self._telemetry_defs["keys"][i]: self._telemetry_defs["definitions"][i]}
            for i in top_similarities_indices
        ]

    def get_telemetry_definition(self, keys: list[str]) -> list[str]:
        """
        Look-up the definition of telemetry keys. Note that the exact telemetry key must be
        known. If not, use `telemetry_key_embedding_lookup`.

        Parameters
        ----------
        keys : str, list[str]
            Telemetry key(s), e.g. ['Speed']

        Returns
        -------
        str, list[str]
            Telemetry definition(s), e.g. ['GPS vehicle speed, m/s']. If a key is not valid, `None` is returned
            at the relevant index.
        """
        return [
            self._telemetry_defs["map"].get(k) for k in keys
        ]
