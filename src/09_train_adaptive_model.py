"""Train and evaluate an adaptive spatiotemporal earthquake forecast.

The experiment is chronological and leakage-safe:

* 2018-2021 origins select the model hyperparameters.
* 2022-2023 origins are an untouched final test.
* Every forecast uses only events at or before its origin.
* All models use identical 60x60 cells, 72-hour windows, and scoring.

The model is intentionally interpretable. It is an ETAS-inspired adaptive rate
model, not a replacement for the operational USGS ETAS family or a claim of
operational earthquake-warning capability.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.special import gammaln
from scipy.stats import poisson

from adaptive_spatiotemporal import (
    AdaptiveConfig,
    adaptive_expected,
    bin_events,
    grid_edges,
    long_term_expected,
    poisson_scores,
    uniform_expected,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "outputs"
DEFAULT_FIGURE_DIR = ROOT / "figures"
DEFAULT_START = "2018-01-01 00:00:00"
DEFAULT_VALIDATION_END = "2021-12-31 23:59:59"
DEFAULT_TEST_START = "2022-01-01 00:00:00"
GRID_ROWS = 60
GRID_COLUMNS = 60
WINDOW_DAYS = 3.0
STEP_DAYS = 3.0
MIN_TRAIN_EVENTS = 365
EPSILON = 1e-12


def parse_args() -> argparse.Namespace:
    """Parse experiment configuration."""
    parser = argparse.ArgumentParser(description="Train and test the ASTR earthquake forecast.")
    parser.add_argument("--catalog", type=Path, default=Path(os.getenv("ADAPTIVE_CATALOG", DEFAULT_CATALOG)))
    parser.add_argument("--output-dir", type=Path, default=Path(os.getenv("ADAPTIVE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR)))
    parser.add_argument("--figure-dir", type=Path, default=Path(os.getenv("ADAPTIVE_FIGURE_DIR", DEFAULT_FIGURE_DIR)))
    parser.add_argument("--start", default=os.getenv("ADAPTIVE_START", DEFAULT_START))
    parser.add_argument("--validation-end", default=os.getenv("ADAPTIVE_VALIDATION_END", DEFAULT_VALIDATION_END))
    parser.add_argument("--test-start", default=os.getenv("ADAPTIVE_TEST_START", DEFAULT_TEST_START))
    parser.add_argument("--end", default=os.getenv("ADAPTIVE_END", ""))
    parser.add_argument("--max-validation-origins", type=int, default=int(os.getenv("ADAPTIVE_MAX_VALIDATION_ORIGINS", "0")))
    parser.add_argument("--max-test-origins", type=int, default=int(os.getenv("ADAPTIVE_MAX_TEST_ORIGINS", "0")))
    return parser.parse_args()


def make_origins(
    catalog: pd.DataFrame,
    start: str,
    end: str,
    min_train_events: int = MIN_TRAIN_EVENTS,
) -> list[pd.Timestamp]:
    """Create deterministic, non-overlapping origins."""
    first = pd.Timestamp(start)
    latest = catalog["datetime"].max() - pd.Timedelta(days=WINDOW_DAYS)
    requested_end = pd.Timestamp(end) if end else latest
    final = min(requested_end, latest)
    candidates = pd.date_range(first, final, freq=pd.Timedelta(days=STEP_DAYS))
    return [origin for origin in candidates if (catalog["datetime"] <= origin).sum() >= min_train_events]


def observed_for_origin(
    catalog: pd.DataFrame,
    origin: pd.Timestamp,
    lons: np.ndarray,
    lats: np.ndarray,
) -> np.ndarray:
    """Return events strictly after origin and through the 72-hour endpoint."""
    end = origin + pd.Timedelta(days=WINDOW_DAYS)
    future = catalog[(catalog["datetime"] > origin) & (catalog["datetime"] <= end)]
    return bin_events(future, lons, lats)


def log_likelihood(expected: np.ndarray, observed: np.ndarray) -> float:
    """Compute Poisson log likelihood without extra scoring overhead."""
    lam = np.maximum(np.asarray(expected, dtype=float), EPSILON)
    return float((observed * np.log(lam) - lam - gammaln(observed + 1.0)).sum())


def candidate_configs() -> list[AdaptiveConfig]:
    """Return the predeclared validation search space."""
    values = itertools.product(
        [7.0, 30.0, 90.0],  # recent window
        [3.0, 7.0, 14.0],  # temporal decay
        [0.0, 0.4],  # magnitude productivity gain
        [0.25, 0.5],  # spatial blend
        [0.0, 0.5, 0.75],  # activity-rate blend
    )
    return [
        AdaptiveConfig(
            recent_window_days=window,
            recent_tau_days=tau,
            magnitude_gain=beta,
            spatial_mix=spatial_mix,
            rate_mix=rate_mix,
        )
        for window, tau, beta, spatial_mix, rate_mix in values
    ]


def train_config(catalog: pd.DataFrame, origins: list[pd.Timestamp], lons: np.ndarray, lats: np.ndarray) -> tuple[AdaptiveConfig, pd.DataFrame]:
    """Select hyperparameters using only the chronological validation block."""
    candidates = candidate_configs()
    records: list[dict] = []
    for candidate_index, config in enumerate(candidates, start=1):
        total_ll = 0.0
        total_events = 0.0
        for origin in origins:
            training = catalog[catalog["datetime"] <= origin]
            observed = observed_for_origin(catalog, origin, lons, lats)
            expected = adaptive_expected(training, origin, lons, lats, WINDOW_DAYS, config)
            total_ll += log_likelihood(expected, observed)
            total_events += observed.sum()
        records.append(
            {
                "candidate": candidate_index,
                **config.to_dict(),
                "validation_log_likelihood": total_ll,
                "validation_observed_events": total_events,
                "validation_log_likelihood_per_event": total_ll / total_events if total_events else float("nan"),
            }
        )
    ranking = pd.DataFrame(records).sort_values("validation_log_likelihood", ascending=False).reset_index(drop=True)
    selected_row = ranking.iloc[0]
    selected = AdaptiveConfig(
        recent_window_days=float(selected_row["recent_window_days"]),
        recent_tau_days=float(selected_row["recent_tau_days"]),
        magnitude_gain=float(selected_row["magnitude_gain"]),
        spatial_mix=float(selected_row["spatial_mix"]),
        rate_mix=float(selected_row["rate_mix"]),
    )
    return selected, ranking


def aggregate(rows: pd.DataFrame) -> list[dict]:
    """Summarize each split/model without hiding per-window rows."""
    summaries: list[dict] = []
    for (split, model), group in rows.groupby(["split", "model"], sort=False):
        expected = float(group["expected_events"].sum())
        observed = float(group["observed_events"].sum())
        low = float(poisson.ppf(0.025, expected))
        high = float(poisson.ppf(0.975, expected))
        summaries.append(
            {
                "split": split,
                "model": model,
                "origins": int(len(group)),
                "total_expected_events": expected,
                "total_observed_events": observed,
                "aggregate_n_test_lower": low,
                "aggregate_n_test_upper": high,
                "aggregate_n_test_pass": bool(low <= observed <= high),
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
    return summaries


def reliability(rows: pd.DataFrame, predictions: dict[tuple[str, str], list[np.ndarray]], observed: dict[str, list[np.ndarray]]) -> dict:
    """Create pooled reliability bins for each split/model."""
    result: dict = {}
    edges = np.linspace(0.0, 1.0, 11)
    for split in ["validation", "test"]:
        result[split] = {}
        labels = np.concatenate([(item.ravel() >= 1) for item in observed[split]])
        for model in ["uniform_rate", "historical_rate", "ASTR"]:
            probs = np.concatenate([item.ravel() for item in predictions[(split, model)]])
            mean_predicted: list[float] = []
            observed_frequency: list[float] = []
            sample_count: list[int] = []
            for left, right in zip(edges[:-1], edges[1:]):
                mask = (probs >= left) & (probs <= right if right == 1 else probs < right)
                sample_count.append(int(mask.sum()))
                mean_predicted.append(float(probs[mask].mean()) if mask.any() else float("nan"))
                observed_frequency.append(float(labels[mask].mean()) if mask.any() else float("nan"))
            result[split][model] = {
                "mean_predicted_probability": mean_predicted,
                "observed_frequency": observed_frequency,
                "sample_count": sample_count,
            }
    return result


def write_figure(rows: pd.DataFrame, figure_path: Path) -> None:
    """Write a compact validation/test diagnostic figure."""
    colors = {"uniform_rate": "#c05621", "historical_rate": "#2b6cb0", "ASTR": "#2f855a"}
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), dpi=150)

    test = rows[rows["split"] == "test"]
    ax = axes[0, 0]
    for model, group in test.groupby("model"):
        ordered = group.sort_values("origin")
        ax.plot(ordered["origin"], ordered["observed_events"], ".", alpha=0.35, color=colors[model])
        ax.plot(ordered["origin"], ordered["expected_events"], linewidth=1.2, color=colors[model], label=model.replace("_", " "))
    ax.set_title("Untouched test: expected vs observed")
    ax.set_ylabel("Events per 72-hour window")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[0, 1]
    auc_values = [test.loc[test["model"] == model, "roc_auc"].dropna() for model in ["uniform_rate", "historical_rate", "ASTR"]]
    ax.boxplot(auc_values, showmeans=True)
    ax.set_xticks([1, 2, 3], ["Uniform", "Historical", "ASTR"])
    ax.axhline(0.5, color="black", linestyle="--", linewidth=1)
    ax.set_ylim(0, 1)
    ax.set_title("Untouched test: per-window AUC")
    ax.set_ylabel("ROC AUC")
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1, 0]
    for model, group in test.groupby("model"):
        ordered = group.sort_values("origin")
        ax.plot(np.arange(len(ordered)), ordered["log_likelihood"].cumsum(), color=colors[model], label=model.replace("_", " "))
    ax.set_title("Untouched test: cumulative log likelihood")
    ax.set_xlabel("Test window")
    ax.set_ylabel("Cumulative Poisson log likelihood")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[1, 1]
    summaries = rows.groupby(["split", "model"], sort=False)["information_gain_per_event"].mean().unstack()
    x = np.arange(len(summaries.index))
    width = 0.25
    for index, model in enumerate(["uniform_rate", "historical_rate", "ASTR"]):
        ax.bar(x + (index - 1) * width, summaries[model], width, color=colors[model], label=model.replace("_", " "))
    ax.set_xticks(x, ["Validation", "Test"])
    ax.axhline(0, color="black", linewidth=1)
    ax.set_title("Mean information gain per event")
    ax.set_ylabel("Against per-window uniform reference")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.25)

    fig.suptitle("Adaptive Spatiotemporal Rate model: chronological experiment", fontsize=14)
    fig.tight_layout()
    figure_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(figure_path)
    plt.close(fig)


def main() -> None:
    """Train ASTR on validation origins and evaluate the untouched test block."""
    args = parse_args()
    if not args.catalog.exists():
        raise FileNotFoundError(f"Missing catalog: {args.catalog}")
    catalog = pd.read_csv(args.catalog, parse_dates=["datetime"]).sort_values("datetime").reset_index(drop=True)
    required = {"datetime", "latitude", "longitude", "magnitude"}
    missing = required - set(catalog.columns)
    if missing:
        raise ValueError(f"Catalog is missing required columns: {sorted(missing)}")

    lons, lats = grid_edges()
    validation_origins = make_origins(catalog, args.start, args.validation_end)
    test_origins = make_origins(catalog, args.test_start, args.end)
    if args.max_validation_origins > 0:
        validation_origins = validation_origins[: args.max_validation_origins]
    if args.max_test_origins > 0:
        test_origins = test_origins[: args.max_test_origins]
    tuning_origins = validation_origins[::4]
    if not tuning_origins:
        raise ValueError("No validation origins available for training.")

    print(f"Validation origins: {len(validation_origins)}; tuning origins: {len(tuning_origins)}")
    print(f"Test origins: {len(test_origins)} ({test_origins[0]} through {test_origins[-1]})")
    selected, ranking = train_config(catalog, tuning_origins, lons, lats)
    print(f"Selected ASTR configuration: {selected.to_dict()}")
    print(f"Validation best log likelihood: {ranking.iloc[0]['validation_log_likelihood']:.2f}")

    origins_by_split = {"validation": validation_origins, "test": test_origins}
    models = ["uniform_rate", "historical_rate", "ASTR"]
    rows: list[dict] = []
    predictions: dict[tuple[str, str], list[np.ndarray]] = {(split, model): [] for split in origins_by_split for model in models}
    observations: dict[str, list[np.ndarray]] = {split: [] for split in origins_by_split}

    for split, origins in origins_by_split.items():
        for index, origin in enumerate(origins, start=1):
            training = catalog[catalog["datetime"] <= origin]
            observed = observed_for_origin(catalog, origin, lons, lats)
            observations[split].append(observed)
            expected_by_model = {
                "uniform_rate": uniform_expected(training, origin, lons, lats, WINDOW_DAYS),
                "historical_rate": long_term_expected(training, origin, lons, lats, WINDOW_DAYS, selected.long_sigma_deg),
                "ASTR": adaptive_expected(training, origin, lons, lats, WINDOW_DAYS, selected),
            }
            for model in models:
                expected = expected_by_model[model]
                scores = poisson_scores(expected, observed)
                predictions[(split, model)].append(1.0 - np.exp(-expected))
                rows.append(
                    {
                        "split": split,
                        "origin": origin.isoformat(),
                        "forecast_end": (origin + pd.Timedelta(days=WINDOW_DAYS)).isoformat(),
                        "training_end": origin.isoformat(),
                        "training_events": int(len(training)),
                        "model": model,
                        "grid_rows": len(lats) - 1,
                        "grid_columns": len(lons) - 1,
                        "grid_step_degrees": 0.1,
                        "window_days": WINDOW_DAYS,
                        "observed_events_in_window": int(observed.sum()),
                        **scores,
                    }
                )
            if index % 100 == 0 or index == len(origins):
                print(f"{split}: scored {index}/{len(origins)} origins")

    per_window = pd.DataFrame(rows)
    summaries = aggregate(per_window)
    summary_by_key = {(item["split"], item["model"]): item for item in summaries}
    test_adaptive = summary_by_key[("test", "ASTR")]
    test_historical = summary_by_key[("test", "historical_rate")]
    test_uniform = summary_by_key[("test", "uniform_rate")]
    comparison = {
        "beats_historical_total_log_likelihood": test_adaptive["total_log_likelihood"] > test_historical["total_log_likelihood"],
        "beats_historical_mean_information_gain": test_adaptive["mean_information_gain_per_event"] > test_historical["mean_information_gain_per_event"],
        "beats_uniform_total_log_likelihood": test_adaptive["total_log_likelihood"] > test_uniform["total_log_likelihood"],
        "test_log_likelihood_delta_vs_historical": test_adaptive["total_log_likelihood"] - test_historical["total_log_likelihood"],
        "test_log_likelihood_delta_vs_uniform": test_adaptive["total_log_likelihood"] - test_uniform["total_log_likelihood"],
        "test_mean_auc_delta_vs_historical": test_adaptive["mean_roc_auc"] - test_historical["mean_roc_auc"],
    }

    output_dir = args.output_dir
    figure_dir = args.figure_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    per_window.to_csv(output_dir / "adaptive_backtest_windows.csv", index=False)
    ranking.to_csv(output_dir / "adaptive_model_tuning.csv", index=False)
    (output_dir / "adaptive_model_config.json").write_text(
        json.dumps(
            {
                "model": "Adaptive Spatiotemporal Rate",
                "abbreviation": "ASTR",
                "description": "Long-term smoothed spatial rate plus recency-weighted, magnitude-weighted activity and spatial adaptation.",
                "operational_context": "ETAS-inspired research model; not a reproduction of USGS OAF or UCERF3-ETAS.",
                "selected_parameters": selected.to_dict(),
                "validation_origins": len(validation_origins),
                "tuning_origins": len(tuning_origins),
                "test_origins": len(test_origins),
                "validation_end": args.validation_end,
                "test_start": args.test_start,
                "training_rule": "datetime <= origin",
                "scoring_rule": "origin < datetime <= origin + 72 hours",
                "grid_rows": GRID_ROWS,
                "grid_columns": GRID_COLUMNS,
                "window_days": WINDOW_DAYS,
                "comparison": comparison,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "adaptive_backtest_summary.json").write_text(
        json.dumps(
            {
                "experiment": "chronological_train_validation_test",
                "catalog": str(args.catalog.relative_to(ROOT)) if args.catalog.is_relative_to(ROOT) else str(args.catalog),
                "operational_benchmark_context": "Standard ETAS-family aftershock forecasting is used operationally by USGS; direct cross-region comparison is not claimed.",
                "validation_origins": len(validation_origins),
                "test_origins": len(test_origins),
                "summary": summaries,
                "comparison": comparison,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "adaptive_backtest_reliability.json").write_text(
        json.dumps(reliability(per_window, predictions, observations), indent=2), encoding="utf-8"
    )
    write_figure(per_window, figure_dir / "adaptive_backtest.png")

    print("\nFinal test comparison:")
    print(pd.DataFrame([test_uniform, test_historical, test_adaptive]).to_string(index=False))
    print(f"\nComparison flags: {json.dumps(comparison, indent=2)}")
    print(f"Saved results to {output_dir}")


if __name__ == "__main__":
    main()
