"""Gardner-Knopoff declustering for a first-order background catalog.

The method scans events from largest to smallest magnitude. Smaller events
inside a larger event's time-distance window are marked as dependent events.
This produces a simple mainshock catalog for ETAS fitting, while preserving the
identified aftershocks for auditability.
"""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
IN_CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
OUT_MAIN = ROOT / "data" / "processed" / "mainshocks_gk.csv"
OUT_AFTER = ROOT / "data" / "processed" / "aftershocks_gk.csv"


def gk_window(magnitude: float) -> tuple[float, float]:
    """Return Gardner-Knopoff time window in days and distance window in km."""
    if magnitude < 3.5:
        return 6.0, 15.0
    if magnitude < 4.5:
        return 11.0, 20.0
    if magnitude < 5.5:
        return 22.0, 30.0
    if magnitude < 6.5:
        return 42.0, 50.0
    if magnitude < 7.5:
        return 83.0, 70.0
    return 155.0, 100.0


def approx_distance_km(lat0: float, lon0: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Approximate horizontal distance using local degree-to-km conversion."""
    dlat_km = (lats - lat0) * 111.0
    dlon_km = (lons - lon0) * 111.0 * np.cos(np.deg2rad(lat0))
    return np.sqrt(dlat_km**2 + dlon_km**2)


def main() -> None:
    """Apply Gardner-Knopoff declustering and save mainshock/aftershock CSVs."""
    if not IN_CATALOG.exists():
        raise FileNotFoundError(f"Run src/02_estimate_mc.py first. Missing: {IN_CATALOG}")

    print(f"Reading Mc-filtered catalog: {IN_CATALOG}")
    df = pd.read_csv(IN_CATALOG, parse_dates=["datetime"]).sort_values("datetime").reset_index(drop=True)
    df["time_days"] = (df["datetime"] - df["datetime"].min()).dt.total_seconds() / 86400.0

    is_aftershock = np.zeros(len(df), dtype=bool)
    parent_event_id = np.full(len(df), fill_value=np.nan)

    order = df.sort_values(["magnitude", "datetime"], ascending=[False, True]).index.to_numpy()
    lats = df["latitude"].to_numpy()
    lons = df["longitude"].to_numpy()
    times = df["time_days"].to_numpy()

    for idx in order:
        if is_aftershock[idx]:
            continue

        days, km = gk_window(float(df.loc[idx, "magnitude"]))
        later = (times > times[idx]) & (times <= times[idx] + days)
        close = approx_distance_km(lats[idx], lons[idx], lats, lons) <= km
        dependent = later & close & (~is_aftershock)
        dependent[idx] = False

        is_aftershock[dependent] = True
        parent_event_id[dependent] = df.loc[idx, "event_id"]

    out = df.drop(columns=["time_days"]).copy()
    out["is_aftershock"] = is_aftershock
    out["parent_event_id"] = parent_event_id

    mainshocks = out[~out["is_aftershock"]].drop(columns=["is_aftershock", "parent_event_id"])
    aftershocks = out[out["is_aftershock"]]

    mainshocks.to_csv(OUT_MAIN, index=False)
    aftershocks.to_csv(OUT_AFTER, index=False)

    print(f"Saved {len(mainshocks)} mainshocks to {OUT_MAIN}")
    print(f"Saved {len(aftershocks)} aftershocks to {OUT_AFTER}")


if __name__ == "__main__":
    main()
