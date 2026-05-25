"""Validate 72-hour probability forecasts against observed JMA catalog data.

Compares one or more forecasts (e.g. ETAS, simple baseline) against the
observed earthquake catalog and reports standard seismological skill scores:
  - Poisson log-likelihood
  - N-test (Number test)
  - ROC curve with AUC
  - Reliability diagram
  - Information gain vs uniform baseline
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
MC_FILE = ROOT / "data" / "outputs" / "mc_estimate.txt"
OUT_DIR = ROOT / "data" / "outputs"
FIG_DIR = ROOT / "figures"

FORECASTS: list[dict] = [
    {
        "label": "ETAS (raw)",
        "grid": ROOT / "data" / "outputs" / "forecast_prob_72h.npy",
        "meta": ROOT / "data" / "outputs" / "forecast_grid_metadata.json",
        "color": "C0",
    },
    {
        "label": "ETAS (calibrated)",
        "grid": ROOT / "data" / "outputs" / "etas_calibrated_prob_72h.npy",
        "meta": ROOT / "data" / "outputs" / "etas_calibrated_grid_metadata.json",
        "color": "C2",
    },
    {
        "label": "Simple (hist. rate)",
        "grid": ROOT / "data" / "outputs" / "simple_prob_72h.npy",
        "meta": ROOT / "data" / "outputs" / "simple_grid_metadata.json",
        "color": "C1",
    },
]

GRID_STEP = 0.1


def bin_observations(
    df: pd.DataFrame,
    lons: np.ndarray,
    lats: np.ndarray,
) -> np.ndarray:
    """Count observed events in each forecast grid cell."""
    ilons = np.searchsorted(lons, df["longitude"].values, side="right") - 1
    ilats = np.searchsorted(lats, df["latitude"].values, side="right") - 1
    valid = (ilons >= 0) & (ilons < len(lons) - 1) & (ilats >= 0) & (ilats < len(lats) - 1)
    obs_grid = np.zeros((len(lats), len(lons)), dtype=float)
    for i, j in zip(ilats[valid], ilons[valid]):
        obs_grid[i, j] += 1
    return obs_grid


def validate_one(
    prob: np.ndarray,
    obs_grid: np.ndarray,
    label: str,
    color: str,
    t0: pd.Timestamp,
    t1: pd.Timestamp,
) -> dict:
    """Compute all metrics for a single forecast and return them."""
    lam = -np.log(np.clip(1 - prob, 1e-12, None))
    n_cells = lam.size
    n_obs = obs_grid.sum()

    # Poisson log-likelihood
    ll = np.sum(stats.poisson.logpmf(obs_grid.ravel(), lam.ravel()))

    # Uniform baseline
    total_expected = lam.sum()
    lam_uniform = np.full_like(lam, total_expected / n_cells)
    ll_uniform = np.sum(stats.poisson.logpmf(obs_grid.ravel(), lam_uniform.ravel()))

    # Information gain
    ig_per_event = (ll - ll_uniform) / max(n_obs, 1)

    # N-test
    n_pred_lo = stats.poisson.ppf(0.025, total_expected)
    n_pred_hi = stats.poisson.ppf(0.975, total_expected)
    n_test_pass = n_pred_lo <= n_obs <= n_pred_hi

    # ROC curve
    thresholds = np.logspace(-3, 0, 200)
    tpr_list: list[float] = []
    fpr_list: list[float] = []
    for thresh in thresholds:
        alarm = (prob >= thresh).ravel()
        obs_bin = (obs_grid >= 1).ravel()
        true_pos = np.sum(alarm & obs_bin)
        false_pos = np.sum(alarm & (~obs_bin))
        cond_pos = np.sum(obs_bin)
        cond_neg = np.sum(~obs_bin)
        tpr_list.append(true_pos / max(cond_pos, 1))
        fpr_list.append(false_pos / max(cond_neg, 1))
    fpr_arr = np.array([0.0] + fpr_list + [1.0])
    tpr_arr = np.array([0.0] + tpr_list + [1.0])
    auc = np.trapezoid(tpr_arr, fpr_arr)

    # Reliability diagram
    n_bins = 10
    bin_edges = np.linspace(0, 1, n_bins + 1)
    mean_pred = np.zeros(n_bins)
    observed_freq = np.zeros(n_bins)
    for i in range(n_bins):
        mask = (prob.ravel() >= bin_edges[i]) & (prob.ravel() < bin_edges[i + 1])
        if mask.sum() > 0:
            mean_pred[i] = prob.ravel()[mask].mean()
            cell_obs = obs_grid.ravel()[mask]
            observed_freq[i] = (cell_obs >= 1).mean()

    print(f"\n--- {label} ---")
    print(f"  Log-likelihood:          {ll:.2f}")
    print(f"  Uniform baseline:        {ll_uniform:.2f}")
    print(f"  Info gain/event:         {ig_per_event:.4f}")
    print(f"  Expected events:         {total_expected:.2f}")
    print(f"  Observed events:         {n_obs:.0f}")
    print(f"  N-test 95% interval:     [{n_pred_lo:.0f}, {n_pred_hi:.0f}]")
    print(f"  N-test:                  {'PASS' if n_test_pass else 'FAIL'}")
    print(f"  ROC AUC:                 {auc:.4f}")

    return {
        "label": label,
        "color": color,
        "ll": ll,
        "ll_uniform": ll_uniform,
        "ig_per_event": ig_per_event,
        "total_expected": total_expected,
        "n_obs": n_obs,
        "n_pred_lo": n_pred_lo,
        "n_pred_hi": n_pred_hi,
        "n_test_pass": n_test_pass,
        "auc": auc,
        "fpr": fpr_arr,
        "tpr": tpr_arr,
        "mean_pred": mean_pred,
        "observed_freq": observed_freq,
        "prob": prob,
    }


def main() -> None:
    if not CATALOG.exists():
        raise FileNotFoundError(f"Run earlier pipeline steps first. Missing: {CATALOG}")

    # Read Mc
    mc_line = next(l for l in MC_FILE.read_text().splitlines() if "Mc_conservative" in l)
    mc = float(mc_line.split("=")[1])

    # Load catalog
    catalog = pd.read_csv(CATALOG, parse_dates=["datetime"])
    print(f"Loaded catalog: {len(catalog)} events, Mc = {mc}")

    # Validate each forecast
    results: list[dict] = []
    for fc in FORECASTS:
        if not fc["grid"].exists():
            print(f"\nSkipping {fc['label']}: {fc['grid']} not found")
            continue
        prob = np.load(fc["grid"])
        meta = json.loads(fc["meta"].read_text())
        lons = np.array(meta["lons"])
        lats = np.array(meta["lats"])
        t0 = pd.Timestamp(meta["t0"])
        t1 = pd.Timestamp(meta["t1"])

        # Filter observations to forecast window
        obs_window = catalog[
            (catalog["datetime"] > t0) & (catalog["datetime"] <= t1)
        ].copy()
        print(f"\n{fc['label']}: {len(obs_window)} observed events in forecast window")

        obs_grid = bin_observations(obs_window, lons, lats)
        result = validate_one(prob, obs_grid, fc["label"], fc["color"], t0, t1)
        result["lons"] = lons
        result["lats"] = lats
        result["t0"] = t0
        result["t1"] = t1
        result["obs_grid"] = obs_grid
        results.append(result)

    if not results:
        print("No forecasts to validate.")
        return

    # Save combined metrics
    metrics_lines = [
        f"forecast_window_start={results[0]['t0'].isoformat()}",
        f"forecast_window_end={results[0]['t1'].isoformat()}",
        f"mc_threshold={mc}",
    ]
    for r in results:
        tag = r["label"].lower().replace(" ", "_").replace("(", "").replace(")", "")
        metrics_lines.extend([
            f"{tag}_log_likelihood={r['ll']:.4f}",
            f"{tag}_log_likelihood_uniform={r['ll_uniform']:.4f}",
            f"{tag}_information_gain_per_event={r['ig_per_event']:.4f}",
            f"{tag}_total_expected_events={r['total_expected']:.4f}",
            f"{tag}_total_observed_events={r['n_obs']:.0f}",
            f"{tag}_n_test_lower={r['n_pred_lo']:.0f}",
            f"{tag}_n_test_upper={r['n_pred_hi']:.0f}",
            f"{tag}_n_test_pass={r['n_test_pass']}",
            f"{tag}_roc_auc={r['auc']:.4f}",
        ])
    (OUT_DIR / "validation_metrics.txt").write_text("\n".join(metrics_lines), encoding="utf-8")
    print(f"\nSaved metrics to {OUT_DIR / 'validation_metrics.txt'}")

    # ---- Figures ----

    # Fig 1: ROC curve (all models)
    fig, ax = plt.subplots(figsize=(6, 5))
    for r in results:
        ax.plot(r["fpr"], r["tpr"], color=r["color"], lw=2, label=f"{r['label']} (AUC = {r['auc']:.3f})")
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5, label="Random")
    ax.set_xlabel("False alarm rate")
    ax.set_ylabel("Hit rate")
    ax.set_title("ROC Curve — 72-hour Forecasts")
    ax.legend(loc="lower right")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "validation_roc.png", dpi=170)
    plt.close(fig)
    print(f"Saved {FIG_DIR / 'validation_roc.png'}")

    # Fig 2: Reliability diagram (all models)
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5, label="Perfect reliability")
    for r in results:
        valid = r["mean_pred"] > 0
        if valid.any():
            ax.plot(r["mean_pred"][valid], r["observed_freq"][valid], "o-", color=r["color"], lw=2, label=r["label"])
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_title("Reliability Diagram — 72-hour Forecasts")
    ax.legend(loc="upper left")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "validation_reliability.png", dpi=170)
    plt.close(fig)
    print(f"Saved {FIG_DIR / 'validation_reliability.png'}")

    # Fig 3: Comparison map (best model + observed)
    best = max(results, key=lambda r: r["auc"])
    fig, axes = plt.subplots(1, len(results) + 1, figsize=(6 * (len(results) + 1), 5))

    for idx, r in enumerate(results):
        ax = axes[idx]
        mesh = ax.pcolormesh(r["lons"], r["lats"], r["prob"], shading="auto", cmap="inferno", vmin=0)
        ax.set_title(f"{r['label']}\nAUC = {r['auc']:.3f}")
        ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
        fig.colorbar(mesh, ax=ax, pad=0.02)
        ax.set_xlim(r["lons"].min(), r["lons"].max())
        ax.set_ylim(r["lats"].min(), r["lats"].max())
        ax.grid(color="white", alpha=0.18, linewidth=0.5)

    # Observed panel
    ax = axes[-1]
    r = results[0]  # Use first result's grid for observed
    ax.pcolormesh(r["lons"], r["lats"], r["obs_grid"], shading="auto", cmap="Blues", vmin=0)
    ax.set_title(f"Observed events\n(N={int(r['n_obs'])})")
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_xlim(r["lons"].min(), r["lons"].max())
    ax.set_ylim(r["lats"].min(), r["lats"].max())
    ax.grid(color="white", alpha=0.18, linewidth=0.5)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "validation_forecast_vs_observed.png", dpi=170)
    plt.close(fig)
    print(f"Saved {FIG_DIR / 'validation_forecast_vs_observed.png'}")


if __name__ == "__main__":
    main()
