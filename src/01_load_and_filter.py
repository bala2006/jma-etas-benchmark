"""Load the manually downloaded JMA catalog and apply basic study filters.

The JMA English search interface does not provide a stable bulk API, so this
project expects a CSV placed at data/raw/jma_tohoku_2010_2023.csv. This script
standardizes column names, parses times, filters the Tohoku/Kanto offshore
region and study period, then writes a clean intermediate catalog.
"""

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
RAW_CATALOG = ROOT / "data" / "raw" / "jma_tohoku_2010_2023.csv"
OUT_CATALOG = ROOT / "data" / "processed" / "catalog_region_2010_2023.csv"

LAT_MIN, LAT_MAX = 36.0, 42.0
LON_MIN, LON_MAX = 140.0, 146.0
START = pd.Timestamp("2010-01-01 00:00:00")
END = pd.Timestamp("2023-12-31 23:59:59")


def main() -> None:
    """Read, clean, and spatially filter the raw JMA hypocenter catalog."""
    if not RAW_CATALOG.exists():
        raise FileNotFoundError(
            "Missing raw catalog. Download the JMA Unified Hypocenter Catalog "
            f"CSV and place it at: {RAW_CATALOG}"
        )

    print(f"Reading raw catalog: {RAW_CATALOG}")
    df = pd.read_csv(RAW_CATALOG)

    required = ["Date", "Time", "Latitude(°N)", "Longitude(°E)", "Depth(km)", "Mag"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Raw catalog is missing required columns: {missing}")

    clean = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                df["Date"].astype(str).str.strip() + " " + df["Time"].astype(str).str.strip(),
                errors="coerce",
            ),
            "latitude": pd.to_numeric(df["Latitude(°N)"], errors="coerce"),
            "longitude": pd.to_numeric(df["Longitude(°E)"], errors="coerce"),
            "depth_km": pd.to_numeric(df["Depth(km)"], errors="coerce"),
            "magnitude": pd.to_numeric(df["Mag"], errors="coerce"),
        }
    )

    before_drop = len(clean)
    clean = clean.dropna(subset=["datetime", "latitude", "longitude", "depth_km", "magnitude"])
    print(f"Dropped {before_drop - len(clean)} rows with missing or invalid values.")

    mask = (
        clean["datetime"].between(START, END)
        & clean["latitude"].between(LAT_MIN, LAT_MAX)
        & clean["longitude"].between(LON_MIN, LON_MAX)
    )
    region = clean.loc[mask].sort_values("datetime").reset_index(drop=True)
    region["event_id"] = range(1, len(region) + 1)
    region = region[["event_id", "datetime", "latitude", "longitude", "depth_km", "magnitude"]]

    OUT_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    region.to_csv(OUT_CATALOG, index=False)
    print(f"Saved {len(region)} events in target region and period to {OUT_CATALOG}")


if __name__ == "__main__":
    main()
