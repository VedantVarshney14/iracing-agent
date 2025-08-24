import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.figure import Figure

from iagent.garage.models import Lap


def plot_lap_telemetry(
    user_lap: Lap,
    user_telem: pd.DataFrame,
) -> Figure:
    fig, axs = plt.subplots(6, 1, figsize=(10, 10), sharex=True)

    for ax, col in zip(axs, ["Speed", "Throttle", "Brake", "SteeringWheelAngle", "Gear", "RPM"]):
        ax.set_ylabel(col)
        ax.grid()
        ax.plot(user_telem[col], color="blue", label=user_lap.driver_slug)

    axs[0].legend(loc="upper right")
    return fig

def plot_track_map(
    telemetry: pd.DataFrame
) -> Figure:
    """
    Plot track map using the telemetry of a lap. Useful for estimating
    turn locations in units of lap dist pct
    """
    fig, ax = plt.subplots()
    # Plot lat and lon as thick grey line, Remove axis ticks and labels
    ax.plot(telemetry["Lon"], telemetry["Lat"], color="grey", linewidth=2)
    ax.set_xticks([])
    ax.set_yticks([])
    # Ensure first telemetry point was collected sufficiently close to the start line
    assert telemetry.loc[0, "LapDistPct"] < 0.001
    # Plot first datapoint as a start line
    ax.plot(
        [telemetry.loc[0, "Lon"], telemetry.loc[1, "Lon"]],
        [telemetry.loc[0, "Lat"], telemetry.loc[1, "Lat"]],
        color="green",
        marker="o",
        markersize=6,
        label="Start"
    )
    # Annotate as start line
    ax.annotate(
        "Start Line",
        (telemetry.loc[0, "Lon"], telemetry.loc[0, "Lat"]),
        textcoords="offset points",
        xytext=(0,10),
        ha='center',
        color="green",
        fontsize=10,
        fontweight="bold"
    )
    # Plot a datapoint every 5% through the lap dist
    for pct in range(10, 100, 5):
        closest_idx = (telemetry["LapDistPct"] - pct / 100).abs().idxmin()
        ax.plot(
            telemetry.loc[closest_idx, "Lon"],
            telemetry.loc[closest_idx, "Lat"],
            color="black",
            marker="o",
            markersize=6
        )
        # Add annotation
        ax.annotate(
            f"{pct}%",
            (telemetry.loc[closest_idx, "Lon"], telemetry.loc[closest_idx, "Lat"]),
            textcoords="offset points",
            xytext=(0,10),
            ha='center',
            color="black",
            fontsize=8,
            fontweight="bold"
        )
    return fig