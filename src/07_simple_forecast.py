"""Generate a 72-hour probability forecast from smoothed historical seismicity.

This is a simple baseline that uses a Gaussian kernel density estimate of past
earthquake locations to forecast where future earthquakes are likely. Unlike the
ETAS model, there is no temporal triggering component — the rate is purely
spatial and derived from the long-term average.

The forecast is saved in the same format as the ETAS forecast so the same
validation pipeline applies.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.ndimage import gaussian_filter


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
MC_FILE = ROOT / "data" / "outputs" / "mc_estimate.txt"
META = ROOT / "data" / "outputs" / "forecast_grid_metadata.json"
OUT_GRID = ROOT / "data" / "outputs" / "simple_prob_72h.npy"
OUT_META = ROOT / "data" / "outputs" / "simple_grid_metadata.json"

LAT_MIN, LAT_MAX = 36.0, 42.0
LON_MIN, LON_MAX = 140.0, 146.0
GRID_STEP = 0.1
WINDOW_DAYS = 3.0
SIGMA_DEG = 0.4  # Gaussian smoothing bandwidth in degrees


def main() -> None:
    if not CATALOG.exists():
        raise FileNotFoundError(f"Run earlier pipeline steps first. Missing: {CATALOG}")

    print(f"Reading catalog: {CATALOG}")
    catalog = pd.read_csv(CATALOG, parse_dates=["datetime"])
    print(f"Total events in catalog: {len(catalog)}")

    # Build grid
    lons = np.round(np.arange(LON_MIN, LON_MAX + GRID_STEP / 2, GRID_STEP), 3)
    lats = np.round(np.arange(LAT_MIN, LAT_MAX + GRID_STEP / 2, GRID_STEP), 3)

    # Compute 2D histogram of all events
    lon_idx = np.searchsorted(lons, catalog["longitude"].values, side="right") - 1
    lat_idx = np.searchsorted(lats, catalog["latitude"].values, side="right") - 1
    valid = (
        (lon_idx >= 0) & (lon_idx < len(lons) - 1) &
        (lat_idx >= 0) & (lat_idx < len(lats) - 1)
    )
    hist = np.zeros((len(lats) - 1, len(lons) - 1), dtype=float)
    for i, j in zip(lat_idx[valid], lon_idx[valid]):
        hist[i, j] += 1.0

    # Apply Gaussian smoothing
    sigma_pixels = SIGMA_DEG / GRID_STEP
    smoothed = gaussian_filter(hist, sigma=sigma_pixels, mode="constant", cval=0.0)

    # Convert to daily rate then to 72-hour probability
    # The histogram counts events in each cell over the full catalog period
    t_start = catalog["datetime"].min()
    t_end = catalog["datetime"].max()
    catalog_days = (t_end - t_start).total_seconds() / 86400.0

    daily_rate = smoothed / catalog_days
    expected_count = daily_rate * WINDOW_DAYS
    probability = 1.0 - np.exp(-expected_count)

    # The smoothed histogram is cell-centered. For pcolormesh we need the
    # cell-corner grid which is the same as the ETAS output.
    prob_full = np.zeros((len(lats), len(lons)), dtype=float)
    prob_full[:-1, :-1] = probability
    # Fill edges from nearest neighbour
    prob_full[-1, :] = prob_full[-2, :]
    prob_full[:, -1] = prob_full[:, -2]

    # Use the same t0/t1 as the ETAS forecast for comparison
    if META.exists():
        ref_meta = json.loads(META.read_text())
        t0 = pd.Timestamp(ref_meta["t0"])
        t1 = pd.Timestamp(ref_meta["t1"])
    else:
        t0 = t_end
        t1 = t0 + pd.Timedelta(days=WINDOW_DAYS)

    # Save
    OUT_GRID.parent.mkdir(parents=True, exist_ok=True)
    np.save(OUT_GRID, prob_full)
    OUT_META.write_text(
        json.dumps(
            {
                "lons": lons.tolist(),
                "lats": lats.tolist(),
                "t0": t0.isoformat(),
                "t1": t1.isoformat(),
                "grid_step_degrees": GRID_STEP,
                "forecast_window_days": WINDOW_DAYS,
                "method": "smoothed_histogram",
                "smoothing_sigma_deg": SIGMA_DEG,
                "catalog_events": len(catalog),
                "catalog_days": round(catalog_days, 1),
                "parameter_source": "historical_rate_baseline",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Saved probability grid to {OUT_GRID}")
    print(f"Total events in catalog: {len(catalog)} over {catalog_days:.0f} days")
    print(f"Expected events in 72h window: {expected_count.sum():.2f}")
    print(f"Probability range: {prob_full.min():.4f} to {prob_full.max():.4f}")


if __name__ == "__main__":
    main()
