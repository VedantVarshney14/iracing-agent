import pandas as pd
from matplotlib import pyplot as plt

from iagent.garage.models import Lap


def plot_lap(
    user_lap: Lap,
    user_telem: pd.DataFrame,
):
    fig, axs = plt.subplots(6, 1, figsize=(10, 10), sharex=True)

    for ax, col in zip(axs, ["Speed", "Throttle", "Brake", "SteeringWheelAngle", "Gear", "RPM"]):
        ax.set_ylabel(col)
        ax.grid()
        ax.plot(user_telem[col], color="blue", label=user_lap.driver_slug)

    axs[0].legend(loc="upper right")
    return fig