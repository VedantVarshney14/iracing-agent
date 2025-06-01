import io
import os

import httpx
import pandas as pd

from iagent.garage.models import Lap


class GarageClient:
    BASE_URL = "https://garage61.net/api/v1"

    def __init__(self):
        if "GARAGE61_PAT" not in os.environ:
            raise ValueError("GARAGE61_PAT environment variable is not set.")

        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {os.environ['GARAGE61_PAT']}"
            }
        )

    @staticmethod
    def _post_process_lap_response(resp: httpx.Response) -> pd.DataFrame:
        resp.raise_for_status()
        laps = pd.json_normalize(resp.json()["items"], sep="_", max_level=1)
        laps["startTime"] = pd.to_datetime(laps["startTime"], utc=True)
        laps = laps.sort_values(
            "startTime", ascending=False
        )
        return laps

    async def get_user_lap(self) -> Lap:
        resp = await self._client.get(
            self.BASE_URL + "/laps",
            params={
                "drivers": "me",
                # Full laps only
                "lapTypes": 1
            }
        )
        laps = self._post_process_lap_response(resp).loc[:1]

        laps["sectorTimes"] = laps["sectors"].apply(
            lambda sectors: None if any(x["incomplete"] for x in sectors) else [x["sectorTime"] for x in sectors]
        )

        return Lap(**laps.loc[0].to_dict())

    async def get_reference_lap(self, lap: Lap, max_delta: int = 2) -> Lap:
        raise NotImplementedError(
            "Public laps are not available via the API. Waiting for response from Garage61 to implement this feature."
        )
        # resp = await self._client.get(
        #     self.BASE_URL + "/laps",
        #     params={
        #         "tracks": [lap.track_id],
        #         "cars": [lap.car_id]
        #     }
        # )
        # laps = self._post_process_lap_response(resp)
        #
        # # Find faster laps by other drivers
        # laps = laps[
        #     (laps["lapTime"] < lap.lapTime) &
        #     (laps["lapTime"] > lap.lapTime - max_delta) &
        #     (laps["driver_slug"] != lap.driver_slug)
        # ]
        #
        # if not len(laps):
        #     raise ValueError(
        #         "No reference lap found within the specified time delta."
        #     )
        #
        # # Select the fastest lap in the time range
        # return Lap(**laps.loc[0].to_dict())

    async def get_lap_telemetry(self, lap_id: str) -> pd.DataFrame:
        resp = await self._client.get(
            self.BASE_URL + f"/laps/{lap_id}/csv",
            headers={"Authorization": f"Bearer {os.environ['GARAGE61_PAT']}"}
        )
        resp.raise_for_status()
        return pd.read_csv(
            io.StringIO(resp.text)
        )
