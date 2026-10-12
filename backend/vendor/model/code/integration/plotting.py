"""INTEGRATION-PLOT: compact observed/simulated hydrograph export."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


def write_hydrograph(
    observed_path: str | Path,
    control_flow_path: str | Path,
    outlet_flow_path: str | Path,
    output_path: str | Path,
    title: str = "Nan'an River 2017 Integrated Runoff-Routing Diagnostic",
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    def read_series(path: str | Path, value_field: str):
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return (
            [datetime.fromisoformat(row["time"]) for row in rows],
            [float(row[value_field]) for row in rows],
        )

    obs_time, observed = read_series(observed_path, "observed_flow_m3_s")
    control_time, control = read_series(control_flow_path, "outflow_cms")
    outlet_time, outlet = read_series(outlet_flow_path, "outflow_cms")

    fig, ax = plt.subplots(figsize=(11.5, 5.2), constrained_layout=True)
    ax.plot(obs_time, observed, color="#202124", linewidth=1.8, label="Observed at Xufan")
    ax.plot(control_time, control, color="#1f77b4", linewidth=1.6, label="Simulated at Xufan")
    ax.plot(outlet_time, outlet, color="#d95f02", linewidth=1.2, label="Simulated basin outlet")
    ax.set_title(title)
    ax.set_ylabel("Discharge (m3/s)")
    ax.set_xlabel("Time")
    ax.grid(True, color="#d9dde3", linewidth=0.7, alpha=0.8)
    ax.legend(frameon=False, ncol=3, loc="upper right")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180)
    plt.close(fig)
