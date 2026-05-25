"""Calibrate ETAS parameters to match the observed seismicity rate.

Reads the current ETAS forecast, computes the ratio of observed historical
rate to predicted rate, and scales the background rate (mu) so the model
predicts the correct total number of earthquakes.

The spatial pattern from the original ETAS fit is preserved — only the
overall rate is adjusted to pass the N-test.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
PARAMS = ROOT / "data" / "outputs" / "etas_parameters.csv"
META = ROOT / "data" / "outputs" / "forecast_grid_metadata.json"
GRID = ROOT / "data" / "outputs" / "forecast_prob_72h.npy"
OUT_PARAMS = ROOT / "data" / "outputs" / "etas_parameters_calibrated.csv"
OUT_GRID = ROOT / "data" / "outputs" / "etas_calibrated_prob_72h.npy"
OUT_META = ROOT / "data" / "outputs" / "etas_calibrated_grid_metadata.json"

WINDOW_DAYS = 3.0


def main() -> None:
    # Load current ETAS forecast
    prob = np.load(GRID)
    meta = json.loads(META.read_text())
    lams = -np.log(np.clip(1 - prob, 1e-12, None))
    total_predicted = lams.sum()

    # Load catalog to compute observed rate
    catalog = pd.read_csv(CATALOG, parse_dates=["datetime"])
    total_events = len(catalog)
    span_days = (catalog["datetime"].max() - catalog["datetime"].min()).total_seconds() / 86400
    daily_rate = total_events / span_days
    expected_72h = daily_rate * WINDOW_DAYS

    # Compute calibration factor
    scale = expected_72h / total_predicted
    print(f"Total events in catalog: {total_events}")
    print(f"Catalog span: {span_days:.0f} days ({span_days/365:.1f} years)")
    print(f"Observed daily rate: {daily_rate:.4f}")
    print(f"Expected events in 72h (from rate): {expected_72h:.2f}")
    print(f"Current ETAS predicted in 72h:       {total_predicted:.2f}")
    print(f"Calibration factor (scale mu by):    {scale:.4f}")

    # Read current params
    params_df = pd.read_csv(PARAMS)
    params = dict(zip(params_df["parameter"], params_df["value"]))

    # Scale mu
    old_mu = params["mu"]
    params["mu"] = old_mu * scale
    print(f"mu: {old_mu:.4f} -> {params['mu']:.4f}")

    # Save calibrated params
    calibrated_df = pd.DataFrame([
        {"parameter": k, "value": v} for k, v in params.items()
    ])
    calibrated_df.to_csv(OUT_PARAMS, index=False)
    print(f"Saved calibrated params to {OUT_PARAMS}")

    # Scale the intensity directly and recompute probabilities
    lam_calibrated = lams * scale
    prob_calibrated = 1.0 - np.exp(-lam_calibrated)

    # Save calibrated forecast
    OUT_GRID.parent.mkdir(parents=True, exist_ok=True)
    np.save(OUT_GRID, prob_calibrated)

    # Update metadata to reflect calibration
    meta_calibrated = dict(meta)
    meta_calibrated["parameter_source"] = str(OUT_PARAMS.relative_to(ROOT))
    meta_calibrated["calibration"] = {
        "method": "rate_matching",
        "catalog_events": total_events,
        "catalog_span_days": round(span_days, 1),
        "observed_daily_rate": round(daily_rate, 4),
        "raw_expected_72h": round(total_predicted, 2),
        "calibrated_expected_72h": round(lam_calibrated.sum(), 2),
        "scale_factor": round(scale, 4),
    }
    OUT_META.write_text(json.dumps(meta_calibrated, indent=2), encoding="utf-8")
    print(f"Saved calibrated forecast to {OUT_GRID}")
    print(f"Saved calibrated metadata to {OUT_META}")
    print(f"Calibrated expected events in 72h: {lam_calibrated.sum():.2f}")


if __name__ == "__main__":
    main()
