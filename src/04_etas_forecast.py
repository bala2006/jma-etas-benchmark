"""Generate a CPU-friendly 72-hour ETAS probability grid in Python.

The canonical fit is performed by the R ETAS package. This fallback reads the
exported parameters and evaluates a simplified conditional intensity on a
0.1-degree grid. The goal is reproducible benchmarking and visualization, not a
replacement for careful version-specific ETAS prediction APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MAINSHOCKS = ROOT / "data" / "processed" / "mainshocks_gk.csv"
PARAMS = ROOT / "data" / "outputs" / "etas_parameters.csv"
OUT_GRID = ROOT / "data" / "outputs" / "forecast_prob_72h.npy"
OUT_META = ROOT / "data" / "outputs" / "forecast_grid_metadata.json"

LAT_MIN, LAT_MAX = 36.0, 42.0
LON_MIN, LON_MAX = 140.0, 146.0
GRID_STEP = 0.1
WINDOW_DAYS = 3.0


def read_parameters(path: Path) -> dict[str, float]:
    """Read ETAS parameters exported from R, with conservative defaults."""
    if not path.exists():
        raise FileNotFoundError(f"Run etas/fit_etas.R first. Missing: {path}")

    params_df = pd.read_csv(path)
    if {"parameter", "value"}.issubset(params_df.columns):
        params = dict(zip(params_df["parameter"], params_df["value"]))
    else:
        params = params_df.iloc[0].to_dict()

    numeric = {str(k): float(v) for k, v in params.items() if pd.notna(v)}
    aliases = {
        "mu": ["mu", "background", "bkgd"],
        "A": ["A", "K", "k"],
        "alpha": ["alpha", "a"],
        "c": ["c"],
        "p": ["p"],
        "D": ["D", "d"],
        "q": ["q"],
        "gamma": ["gamma"],
    }

    resolved: dict[str, float] = {}
    for canonical, names in aliases.items():
        for name in names:
            if name in numeric:
                resolved[canonical] = numeric[name]
                break

    # Defaults are deliberately modest so the script remains runnable if the
    # installed ETAS version names parameters differently.
    resolved.setdefault("mu", 1e-4)
    resolved.setdefault("A", 0.01)
    resolved.setdefault("alpha", 1.0)
    resolved.setdefault("c", 0.01)
    resolved.setdefault("p", 1.1)
    resolved.setdefault("D", 0.05)
    resolved.setdefault("q", 1.8)
    resolved.setdefault("gamma", 0.5)
    resolved["D"] = max(resolved["D"], 1e-6)
    resolved["c"] = max(resolved["c"], 1e-6)
    resolved["p"] = max(resolved["p"], 1.001)
    resolved["q"] = max(resolved["q"], 1.001)
    resolved["alpha"] = float(np.clip(resolved["alpha"], -5.0, 5.0))
    resolved["gamma"] = float(np.clip(resolved["gamma"], -5.0, 5.0))
    return resolved


def main() -> None:
    """Evaluate the forecast grid and save probabilities plus metadata."""
    if not MAINSHOCKS.exists():
        raise FileNotFoundError(f"Run src/03_decluster.py first. Missing: {MAINSHOCKS}")

    print(f"Reading mainshocks: {MAINSHOCKS}")
    events = pd.read_csv(MAINSHOCKS, parse_dates=["datetime"]).sort_values("datetime")
    params = read_parameters(PARAMS)
    print(f"Using ETAS parameters: {params}")

    lons = np.round(np.arange(LON_MIN, LON_MAX + GRID_STEP / 2, GRID_STEP), 3)
    lats = np.round(np.arange(LAT_MIN, LAT_MAX + GRID_STEP / 2, GRID_STEP), 3)
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    t0 = events["datetime"].max() - pd.Timedelta(days=3)
    t1 = t0 + pd.Timedelta(days=WINDOW_DAYS)

    events = events[events["datetime"] <= t0].copy()

    event_times = (t0 - events["datetime"]).dt.total_seconds().to_numpy() / 86400.0
    event_lats = events["latitude"].to_numpy()
    event_lons = events["longitude"].to_numpy()
    event_mags = events["magnitude"].to_numpy()
    m0 = float(event_mags.min())

    intensity = np.full(lon_grid.shape, params["mu"], dtype=float)
    for age, ev_lat, ev_lon, mag in zip(event_times, event_lats, event_lons, event_mags):
        # Productivity grows with magnitude; temporal decay follows Omori law.
        productivity = params["A"] * np.exp(params["alpha"] * (mag - m0))
        temporal = (age + params["c"]) ** (-params["p"])

        # The R fit uses ETAS::catalog(dist.unit="degree"), so D and the grid
        # integral are evaluated in square degrees rather than square km.
        dx = lon_grid - ev_lon
        dy = lat_grid - ev_lat
        r2 = dx**2 + dy**2
        spatial_scale = params["D"] * np.exp(params["gamma"] * (mag - m0))
        spatial = ((params["q"] - 1.0) / (np.pi * spatial_scale)) * (
            1.0 + r2 / spatial_scale
        ) ** (-params["q"])
        intensity += productivity * temporal * spatial

    cell_area_degrees = GRID_STEP * GRID_STEP
    expected_count = np.maximum(intensity * WINDOW_DAYS * cell_area_degrees, 0.0)
    probability = 1.0 - np.exp(-expected_count)

    OUT_GRID.parent.mkdir(parents=True, exist_ok=True)
    np.save(OUT_GRID, probability)
    OUT_META.write_text(
        json.dumps(
            {
                "lons": lons.tolist(),
                "lats": lats.tolist(),
                "t0": t0.isoformat(),
                "t1": t1.isoformat(),
                "grid_step_degrees": GRID_STEP,
                "forecast_window_days": WINDOW_DAYS,
                "parameter_source": str(PARAMS.relative_to(ROOT)),
                "effective_parameters": params,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Saved probability grid to {OUT_GRID}")
    print(f"Saved grid metadata to {OUT_META}")


if __name__ == "__main__":
    main()
