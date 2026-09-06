"""Run a prospective-style rolling audit for simple spatial rate forecasts.

This audit deliberately evaluates models that can be trained without the R
ETAS dependency. Every forecast uses only events at or before its origin, and
the next 72 hours are kept for scoring. It is a reliable multi-window
benchmark for the historical-rate baseline, not a substitute for a rolling
ETAS refit.

The forecast grid uses 60 x 60 cells and 61 x 61 coordinate edges. Keeping
cell values separate from coordinate edges avoids counting the final edge row
or column as an additional forecast cell.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
DEFAULT_OUT_DIR = ROOT / "data" / "outputs"
DEFAULT_FIG_DIR = ROOT / "figures"

LAT_MIN, LAT_MAX = 36.0, 42.0
LON_MIN, LON_MAX = 140.0, 146.0
GRID_STEP = 0.1
DEFAULT_WINDOW_DAYS = 3.0
DEFAULT_START = "2018-01-01 00:00:00"
DEFAULT_MIN_TRAIN_EVENTS = 365
DEFAULT_SIGMA_DEG = 0.4
EPSILON = 1e-12


def parse_args() -> argparse.Namespace:
    """Parse command-line options, with environment-variable overrides."""
    parser = argparse.ArgumentParser(
        description="Evaluate training-only spatial baselines over rolling 72-hour windows."
    )
    parser.add_argument("--catalog", type=Path, default=Path(os.getenv("BACKTEST_CATALOG", DEFAULT_CATALOG)))
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("BACKTEST_OUTPUT_DIR", DEFAULT_OUT_DIR)))
    parser.add_argument("--figure-dir", type=Path, default=Path(os.getenv("BACKTEST_FIGURE_DIR", DEFAULT_FIG_DIR)))
    parser.add_argument("--start", default=os.getenv("BACKTEST_START", DEFAULT_START))
    parser.add_argument("--end", default=os.getenv("BACKTEST_END", ""))
    parser.add_argument("--step-days", type=float, default=float(os.getenv("BACKTEST_STEP_DAYS", "3")))
    parser.add_argument("--window-days", type=float, default=float(os.getenv("BACKTEST_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)))
    parser.add_argument("--min-train-events", type=int, default=int(os.getenv("BACKTEST_MIN_TRAIN_EVENTS", DEFAULT_MIN_TRAIN_EVENTS)))
    parser.add_argument("--sigma-deg", type=float, default=float(os.getenv("BACKTEST_SIGMA_DEG", DEFAULT_SIGMA_DEG)))
    parser.add_argument("--max-windows", type=int, default=int(os.getenv("BACKTEST_MAX_WINDOWS", "0")))
    return parser.parse_args()


def grid_edges() -> tuple[np.ndarray, np.ndarray]:
    """Return longitude and latitude cell edges for the study region."""
    lons = np.round(np.arange(LON_MIN, LON_MAX + GRID_STEP / 2, GRID_STEP), 3)
    lats = np.round(np.arange(LAT_MIN, LAT_MAX + GRID_STEP / 2, GRID_STEP), 3)
    return lons, lats


def bin_events(df: pd.DataFrame, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Count events in cells defined by coordinate edges."""
    histogram = np.zeros((len(lats) - 1, len(lons) - 1), dtype=float)
    lon_idx = np.searchsorted(lons, df["longitude"].to_numpy(), side="right") - 1
    lat_idx = np.searchsorted(lats, df["latitude"].to_numpy(), side="right") - 1
    valid = (
        (lon_idx >= 0)
        & (lon_idx < len(lons) - 1)
        & (lat_idx >= 0)
        & (lat_idx < len(lats) - 1)
    )
    np.add.at(histogram, (lat_idx[valid], lon_idx[valid]), 1.0)
    return histogram


def training_span_days(catalog: pd.DataFrame, origin: pd.Timestamp) -> float:
    """Return the available training duration from catalog start to origin."""
    first = catalog["datetime"].min()
    span = (origin - first).total_seconds() / 86400.0
    if span <= 0:
        raise ValueError(f"Origin {origin} does not follow the first catalog event {first}.")
    return span


def expected_counts(
    training: pd.DataFrame,
    origin: pd.Timestamp,
    lons: np.ndarray,
    lats: np.ndarray,
    window_days: float,
    sigma_deg: float,
    model: str,
) -> np.ndarray:
    """Create expected event counts for one origin using training data only."""
    span_days = training_span_days(training, origin)
    n_cells = (len(lats) - 1) * (len(lons) - 1)
    total_expected = len(training) / span_days * window_days

    if model == "uniform_rate":
        return np.full((len(lats) - 1, len(lons) - 1), total_expected / n_cells)
    if model != "historical_rate":
        raise ValueError(f"Unsupported model: {model}")

    counts = bin_events(training, lons, lats)
    smoothed = gaussian_filter(
        counts,
        sigma=sigma_deg / GRID_STEP,
        mode="constant",
        cval=0.0,
    )
    # Boundary padding in a Gaussian filter loses mass. Renormalize so this
    # baseline's total rate is exactly the training-only rate.
    if smoothed.sum() <= 0:
        raise ValueError("Spatial smoothing produced zero mass.")
    smoothed *= counts.sum() / smoothed.sum()
    return smoothed / span_days * window_days


def observed_counts(
    catalog: pd.DataFrame,
    origin: pd.Timestamp,
    window_days: float,
    lons: np.ndarray,
    lats: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Select strictly future events and bin them in the forecast cells."""
    end = origin + pd.Timedelta(days=window_days)
    future = catalog[(catalog["datetime"] > origin) & (catalog["datetime"] <= end)]
    return bin_events(future, lons, lats), len(future)


def auc_score(prob: np.ndarray, observed: np.ndarray) -> float:
    """Compute ROC AUC from ranks, returning NaN for degenerate windows."""
    labels = (observed.ravel() >= 1).astype(int)
    positives = labels.sum()
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return float("nan")
    ranks = stats.rankdata(prob.ravel(), method="average")
    positive_rank_sum = ranks[labels == 1].sum()
    return float((positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives))


def score_forecast(expected: np.ndarray, observed: np.ndarray) -> dict[str, float | bool]:
    """Score a single forecast with count, spatial, and probability metrics."""
    lam = np.maximum(expected, 0.0)
    probability = 1.0 - np.exp(-lam)
    total_expected = float(lam.sum())
    n_observed = float(observed.sum())
    n_cells = lam.size
    log_likelihood = float(stats.poisson.logpmf(observed.ravel(), lam.ravel()).sum())
    uniform_lam = np.full_like(lam, total_expected / n_cells)
    uniform_ll = float(stats.poisson.logpmf(observed.ravel(), uniform_lam.ravel()).sum())
    n_lo = float(stats.poisson.ppf(0.025, total_expected))
    n_hi = float(stats.poisson.ppf(0.975, total_expected))
    observed_binary = (observed.ravel() >= 1).astype(float)
    brier = float(np.mean((probability.ravel() - observed_binary) ** 2))
    return {
        "expected_events": total_expected,
        "observed_events": n_observed,
        "log_likelihood": log_likelihood,
        "uniform_log_likelihood": uniform_ll,
        "information_gain_per_event": (log_likelihood - uniform_ll) / n_observed if n_observed else float("nan"),
        "n_test_lower": n_lo,
        "n_test_upper": n_hi,
        "n_test_pass": bool(n_lo <= n_observed <= n_hi),
        "roc_auc": auc_score(probability, observed),
        "brier_score": brier,
    }


def make_origins(
    catalog: pd.DataFrame,
    start: str,
    end: str,
    step_days: float,
    window_days: float,
    min_train_events: int,
    max_windows: int,
) -> list[pd.Timestamp]:
    """Create deterministic non-overlapping origins with enough history."""
    if step_days < window_days:
        raise ValueError("step-days must be at least window-days to avoid overlapping windows.")
    start_time = pd.Timestamp(start)
    max_origin = catalog["datetime"].max() - pd.Timedelta(days=window_days)
    end_time = pd.Timestamp(end) if end else max_origin
    end_time = min(end_time, max_origin)
    candidates = pd.date_range(start_time, end_time, freq=pd.Timedelta(days=step_days))
    origins = [origin for origin in candidates if (catalog["datetime"] <= origin).sum() >= min_train_events]
    if max_windows > 0:
        origins = origins[:max_windows]
    if not origins:
        raise ValueError("No valid origins. Lower min-train-events or adjust the date range.")
    return origins


def pooled_reliability(
    predictions: dict[str, list[np.ndarray]],
    observations: list[np.ndarray],
    n_bins: int = 10,
) -> dict[str, dict[str, list[float]]]:
    """Aggregate reliability bins across all windows and models."""
    result: dict[str, dict[str, list[float]]] = {}
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    binary_observations = [obs.ravel() >= 1 for obs in observations]
    for model, grids in predictions.items():
        probs = np.concatenate([grid.ravel() for grid in grids])
        labels = np.concatenate(binary_observations)
        mean_pred: list[float] = []
        observed_freq: list[float] = []
        counts: list[int] = []
        for left, right in zip(edges[:-1], edges[1:]):
            mask = (probs >= left) & (probs < right if right < 1 else probs <= right)
            counts.append(int(mask.sum()))
            mean_pred.append(float(probs[mask].mean()) if mask.any() else float("nan"))
            observed_freq.append(float(labels[mask].mean()) if mask.any() else float("nan"))
        result[model] = {
            "mean_predicted_probability": mean_pred,
            "observed_frequency": observed_freq,
            "sample_count": counts,
        }
    return result


def write_figure(
    per_window: pd.DataFrame,
    reliability: dict[str, dict[str, list[float]]],
    figure_path: Path,
) -> None:
    """Write a compact aggregate diagnostic figure."""
    colors = {"historical_rate": "#2b6cb0", "uniform_rate": "#c05621"}
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), dpi=150)

    ax = axes[0, 0]
    for model, group in per_window.groupby("model"):
        ax.plot(
            group["origin"],
            group["observed_events"],
            ".",
            color=colors[model],
            alpha=0.45,
            label=f"{model.replace('_', ' ').title()} observed",
        )
        ax.plot(
            group["origin"],
            group["expected_events"],
            "-",
            color=colors[model],
            linewidth=1.3,
            label=f"{model.replace('_', ' ').title()} expected",
        )
    ax.set_title("Expected versus observed count")
    ax.set_ylabel("Events in 72-hour window")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.25)

    ax = axes[0, 1]
    auc_data = [per_window.loc[per_window["model"] == model, "roc_auc"].dropna() for model in colors]
    ax.boxplot(auc_data, showmeans=True)
    ax.set_xticks([1, 2], ["Historical\nrate", "Uniform\nrate"])
    ax.axhline(0.5, color="black", linestyle="--", linewidth=1, label="Random")
    ax.set_title("Per-window ROC AUC")
    ax.set_ylabel("AUC")
    ax.set_ylim(0, 1)
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1, 0]
    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="Perfect reliability")
    for model, values in reliability.items():
        x = np.asarray(values["mean_predicted_probability"], dtype=float)
        y = np.asarray(values["observed_frequency"], dtype=float)
        valid = np.isfinite(x) & np.isfinite(y)
        ax.plot(x[valid], y[valid], "o-", color=colors[model], label=model.replace("_", " ").title())
    ax.set_title("Pooled reliability across windows")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[1, 1]
    for model, group in per_window.groupby("model"):
        cumulative = group["information_gain_per_event"].fillna(0).cumsum()
        ax.plot(np.arange(1, len(cumulative) + 1), cumulative, color=colors[model], label=model.replace("_", " ").title())
    ax.axhline(0, color="black", linewidth=1)
    ax.set_title("Cumulative information gain per event")
    ax.set_xlabel("Forecast window")
    ax.set_ylabel("Cumulative IG/event")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    fig.suptitle("Rolling-origin baseline audit (training-only forecasts)", fontsize=14)
    fig.tight_layout()
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure_path)
    plt.close(fig)


def aggregate_summary(per_window: pd.DataFrame) -> list[dict]:
    """Build model-level summary statistics and aggregate N-test result."""
    rows: list[dict] = []
    for model, group in per_window.groupby("model", sort=False):
        total_expected = float(group["expected_events"].sum())
        total_observed = float(group["observed_events"].sum())
        lower = float(stats.poisson.ppf(0.025, total_expected))
        upper = float(stats.poisson.ppf(0.975, total_expected))
        rows.append(
            {
                "model": model,
                "windows": int(len(group)),
                "total_expected_events": total_expected,
                "total_observed_events": total_observed,
                "aggregate_n_test_lower": lower,
                "aggregate_n_test_upper": upper,
                "aggregate_n_test_pass": bool(lower <= total_observed <= upper),
                "window_n_test_pass_rate": float(group["n_test_pass"].mean()),
                "mean_log_likelihood": float(group["log_likelihood"].mean()),
                "total_log_likelihood": float(group["log_likelihood"].sum()),
                "mean_information_gain_per_event": float(group["information_gain_per_event"].mean()),
                "mean_roc_auc": float(group["roc_auc"].mean()),
                "median_roc_auc": float(group["roc_auc"].median()),
                "auc_p05": float(group["roc_auc"].quantile(0.05)),
                "auc_p95": float(group["roc_auc"].quantile(0.95)),
                "mean_brier_score": float(group["brier_score"].mean()),
            }
        )
    return rows


def main() -> None:
    """Run the rolling audit and save machine-readable and visual results."""
    args = parse_args()
    if not args.catalog.exists():
        raise FileNotFoundError(f"Missing catalog: {args.catalog}")
    if args.window_days <= 0 or args.sigma_deg <= 0:
        raise ValueError("window-days and sigma-deg must be positive.")

    catalog = pd.read_csv(args.catalog, parse_dates=["datetime"]).sort_values("datetime").reset_index(drop=True)
    required = {"datetime", "latitude", "longitude"}
    missing = required - set(catalog.columns)
    if missing:
        raise ValueError(f"Catalog is missing required columns: {sorted(missing)}")

    lons, lats = grid_edges()
    origins = make_origins(
        catalog,
        args.start,
        args.end,
        args.step_days,
        args.window_days,
        args.min_train_events,
        args.max_windows,
    )
    models = ["historical_rate", "uniform_rate"]
    rows: list[dict] = []
    predictions: dict[str, list[np.ndarray]] = {model: [] for model in models}
    observations: list[np.ndarray] = []

    print(f"Catalog events: {len(catalog)}")
    print(f"Origins: {len(origins)} ({origins[0]} through {origins[-1]})")
    print(f"Window: {args.window_days:g} days; step: {args.step_days:g} days; cells: {(len(lats)-1)}x{(len(lons)-1)}")

    for index, origin in enumerate(origins, start=1):
        training = catalog[catalog["datetime"] <= origin].copy()
        observed, observed_count = observed_counts(catalog, origin, args.window_days, lons, lats)
        observations.append(observed)
        span_days = training_span_days(catalog, origin)
        for model in models:
            expected = expected_counts(training, origin, lons, lats, args.window_days, args.sigma_deg, model)
            scored = score_forecast(expected, observed)
            predictions[model].append(1.0 - np.exp(-expected))
            rows.append(
                {
                    "origin": origin.isoformat(),
                    "forecast_end": (origin + pd.Timedelta(days=args.window_days)).isoformat(),
                    "training_end": origin.isoformat(),
                    "training_events": int(len(training)),
                    "training_span_days": round(span_days, 4),
                    "model": model,
                    "grid_rows": len(lats) - 1,
                    "grid_columns": len(lons) - 1,
                    "grid_step_degrees": GRID_STEP,
                    "window_days": args.window_days,
                    "smoothing_sigma_deg": args.sigma_deg if model == "historical_rate" else 0.0,
                    "observed_events_in_window": observed_count,
                    **scored,
                }
            )
        if index % 100 == 0 or index == len(origins):
            print(f"Scored {index}/{len(origins)} origins")

    per_window = pd.DataFrame(rows)
    summary = aggregate_summary(per_window)
    reliability = pooled_reliability(predictions, observations)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir = args.figure_dir
    per_window_path = output_dir / "rolling_backtest_windows.csv"
    summary_path = output_dir / "rolling_backtest_summary.json"
    reliability_path = output_dir / "rolling_backtest_reliability.json"
    figure_path = figure_dir / "rolling_backtest.png"
    per_window.to_csv(per_window_path, index=False)

    metadata = {
        "evaluation_type": "rolling_origin_baseline_audit",
        "catalog": str(args.catalog.relative_to(ROOT)) if args.catalog.is_relative_to(ROOT) else str(args.catalog),
        "training_rule": "events with datetime <= forecast origin",
        "scoring_rule": "events with origin < datetime <= origin + window_days",
        "window_overlap": False,
        "models": models,
        "etas_status": "not refit in this Python-only audit",
        "interpretation": "This report establishes a prospective-style baseline reference; it does not claim rolling ETAS skill.",
        "origins": len(origins),
        "origin_start": origins[0].isoformat(),
        "origin_end": origins[-1].isoformat(),
        "window_days": args.window_days,
        "step_days": args.step_days,
        "grid": {
            "longitude_edges": lons.tolist(),
            "latitude_edges": lats.tolist(),
            "rows": len(lats) - 1,
            "columns": len(lons) - 1,
            "step_degrees": GRID_STEP,
        },
        "summary": summary,
    }
    summary_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    reliability_path.write_text(json.dumps(reliability, indent=2), encoding="utf-8")
    write_figure(per_window, reliability, figure_path)

    print(f"Saved per-window results: {per_window_path}")
    print(f"Saved summary: {summary_path}")
    print(f"Saved reliability data: {reliability_path}")
    print(f"Saved figure: {figure_path}")
    print("\nAggregate results:")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()
