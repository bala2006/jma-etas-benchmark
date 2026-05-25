"""Visualize the 72-hour ETAS probability grid with observed M4+ events."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
GRID = ROOT / "data" / "outputs" / "forecast_prob_72h.npy"
META = ROOT / "data" / "outputs" / "forecast_grid_metadata.json"
CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
OUT_FIG = ROOT / "figures" / "forecast_map_72h.png"


def main() -> None:
    """Create a publication-quality probability map."""
    for path in [GRID, META, CATALOG]:
        if not path.exists():
            raise FileNotFoundError(f"Missing required file: {path}")

    print("Loading forecast grid and metadata.")
    prob = np.load(GRID)
    meta = json.loads(META.read_text(encoding="utf-8"))
    lons = np.array(meta["lons"])
    lats = np.array(meta["lats"])
    t0 = pd.Timestamp(meta["t0"])
    t1 = pd.Timestamp(meta["t1"])

    catalog = pd.read_csv(CATALOG, parse_dates=["datetime"])
    observed = catalog[
        (catalog["datetime"] > t0)
        & (catalog["datetime"] <= t1)
        & (catalog["magnitude"] >= 4.0)
    ]

    fig, ax = plt.subplots(figsize=(8, 7), dpi=170)
    mesh = ax.pcolormesh(lons, lats, prob, shading="auto", cmap="inferno", vmin=0.0)
    if not observed.empty:
        ax.scatter(
            observed["longitude"],
            observed["latitude"],
            s=np.clip((observed["magnitude"] - 3.5) ** 3 * 20, 25, 250),
            facecolors="none",
            edgecolors="#67e8f9",
            linewidths=1.3,
            label="Observed M4+",
        )
        ax.legend(loc="upper right", frameon=True)

    cbar = fig.colorbar(mesh, ax=ax, pad=0.02)
    cbar.set_label("72-hour earthquake probability")
    ax.set_xlabel("Longitude (degrees E)")
    ax.set_ylabel("Latitude (degrees N)")
    ax.set_title(f"ETAS 72-hour Forecast: {t0:%Y-%m-%d} to {t1:%Y-%m-%d}")
    ax.set_xlim(lons.min(), lons.max())
    ax.set_ylim(lats.min(), lats.max())
    ax.grid(color="white", alpha=0.18, linewidth=0.5)
    fig.tight_layout()

    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG)
    plt.close(fig)
    print(f"Saved forecast map to {OUT_FIG}")


if __name__ == "__main__":
    main()
