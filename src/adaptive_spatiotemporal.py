"""Leakage-safe adaptive spatiotemporal rate model.

The model is an interpretable ETAS-inspired benchmark. It does not claim to be
an implementation of the full R ETAS package: it combines a long-term spatial
rate field with a recency-weighted spatial field and a magnitude-weighted
activity multiplier. All quantities are computed from a training prefix.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter
from scipy.special import gammaln

GRID_STEP = 0.1
EPSILON = 1e-12


@dataclass(frozen=True)
class AdaptiveConfig:
    """Hyperparameters learned on chronological validation origins."""

    recent_window_days: float
    recent_tau_days: float
    magnitude_gain: float
    spatial_mix: float
    rate_mix: float
    long_sigma_deg: float = 0.4
    recent_sigma_deg: float = 0.2
    mc: float = 0.8

    def to_dict(self) -> dict[str, float]:
        """Return JSON-friendly model parameters."""
        return {key: float(value) for key, value in asdict(self).items()}


def grid_edges(
    lat_min: float = 36.0,
    lat_max: float = 42.0,
    lon_min: float = 140.0,
    lon_max: float = 146.0,
    step: float = GRID_STEP,
) -> tuple[np.ndarray, np.ndarray]:
    """Return longitude and latitude cell edges."""
    lons = np.round(np.arange(lon_min, lon_max + step / 2, step), 3)
    lats = np.round(np.arange(lat_min, lat_max + step / 2, step), 3)
    return lons, lats


def bin_events(df: pd.DataFrame, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Count events inside cells defined by coordinate edges."""
    counts = np.zeros((len(lats) - 1, len(lons) - 1), dtype=float)
    if df.empty:
        return counts
    lon_idx = np.searchsorted(lons, df["longitude"].to_numpy(), side="right") - 1
    lat_idx = np.searchsorted(lats, df["latitude"].to_numpy(), side="right") - 1
    lon_idx = np.where(lon_idx == len(lons) - 1, len(lons) - 2, lon_idx)
    lat_idx = np.where(lat_idx == len(lats) - 1, len(lats) - 2, lat_idx)
    valid = (
        (lon_idx >= 0)
        & (lon_idx < len(lons) - 1)
        & (lat_idx >= 0)
        & (lat_idx < len(lats) - 1)
    )
    np.add.at(counts, (lat_idx[valid], lon_idx[valid]), 1.0)
    return counts


def _normalized_smooth(counts: np.ndarray, sigma_deg: float) -> np.ndarray:
    """Smooth counts and normalize them into a spatial probability field."""
    if counts.sum() <= 0:
        return np.full_like(counts, 1.0 / counts.size)
    smoothed = gaussian_filter(
        counts,
        sigma=max(sigma_deg / GRID_STEP, 0.01),
        mode="constant",
        cval=0.0,
    )
    if smoothed.sum() <= 0:
        return np.full_like(counts, 1.0 / counts.size)
    return smoothed / smoothed.sum()


def _training_span_days(training: pd.DataFrame, origin: pd.Timestamp) -> float:
    """Return the duration represented by a training prefix."""
    span = (origin - training["datetime"].min()).total_seconds() / 86400.0
    if span <= 0:
        raise ValueError(f"Training prefix has no positive span before {origin}.")
    return span


def long_term_expected(
    training: pd.DataFrame,
    origin: pd.Timestamp,
    lons: np.ndarray,
    lats: np.ndarray,
    window_days: float,
    sigma_deg: float = 0.4,
) -> np.ndarray:
    """Expected counts from the training-only long-term spatial rate."""
    span_days = _training_span_days(training, origin)
    total_expected = len(training) / span_days * window_days
    spatial = _normalized_smooth(bin_events(training, lons, lats), sigma_deg)
    return total_expected * spatial


def uniform_expected(
    training: pd.DataFrame,
    origin: pd.Timestamp,
    lons: np.ndarray,
    lats: np.ndarray,
    window_days: float,
) -> np.ndarray:
    """Expected counts from a training-only uniform spatial rate."""
    total_expected = len(training) / _training_span_days(training, origin) * window_days
    return np.full((len(lats) - 1, len(lons) - 1), total_expected / ((len(lats) - 1) * (len(lons) - 1)))


def adaptive_expected(
    training: pd.DataFrame,
    origin: pd.Timestamp,
    lons: np.ndarray,
    lats: np.ndarray,
    window_days: float,
    config: AdaptiveConfig,
) -> np.ndarray:
    """Generate expected counts from the trained ASTR configuration.

    The long-term spatial field supplies the stable geographic prior. Recent
    events modify both spatial concentration and total activity, with the
    expected recency weight normalized by the training event rate. This keeps
    the activity multiplier interpretable and prevents a change in catalog
    length from silently changing the model scale.
    """
    span_days = _training_span_days(training, origin)
    training_count = len(training)
    base_total = training_count / span_days * window_days
    long_spatial = _normalized_smooth(
        bin_events(training, lons, lats), config.long_sigma_deg
    )

    ages = (origin - training["datetime"]).dt.total_seconds().to_numpy() / 86400.0
    recent_mask = (ages >= 0.0) & (ages <= config.recent_window_days)
    recent = training.loc[recent_mask].copy()
    recent_ages = ages[recent_mask]

    if recent.empty:
        return base_total * long_spatial

    magnitudes = recent["magnitude"].to_numpy(dtype=float)
    magnitude_weights = np.exp(
        np.clip(config.magnitude_gain * (magnitudes - config.mc), -8.0, 8.0)
    )
    training_magnitude_weights = np.exp(
        np.clip(config.magnitude_gain * (training["magnitude"].to_numpy(dtype=float) - config.mc), -8.0, 8.0)
    )
    magnitude_weights /= max(float(training_magnitude_weights.mean()), EPSILON)
    temporal_weights = np.exp(-recent_ages / max(config.recent_tau_days, EPSILON))
    weights = magnitude_weights * temporal_weights

    recent_counts = bin_events(recent, lons, lats)
    recent_weighted = np.zeros_like(recent_counts)
    lon_idx = np.searchsorted(lons, recent["longitude"].to_numpy(), side="right") - 1
    lat_idx = np.searchsorted(lats, recent["latitude"].to_numpy(), side="right") - 1
    lon_idx = np.where(lon_idx == len(lons) - 1, len(lons) - 2, lon_idx)
    lat_idx = np.where(lat_idx == len(lats) - 1, len(lats) - 2, lat_idx)
    valid = (
        (lon_idx >= 0)
        & (lon_idx < len(lons) - 1)
        & (lat_idx >= 0)
        & (lat_idx < len(lats) - 1)
    )
    np.add.at(recent_weighted, (lat_idx[valid], lon_idx[valid]), weights[valid])
    recent_spatial = _normalized_smooth(recent_weighted, config.recent_sigma_deg)

    expected_weighted_recent = (
        training_count / span_days
        * config.recent_tau_days
        * (1.0 - np.exp(-config.recent_window_days / config.recent_tau_days))
    )
    activity_ratio = float(weights.sum()) / max(expected_weighted_recent, EPSILON)
    activity_ratio = float(np.clip(activity_ratio, 0.25, 4.0))
    rate_multiplier = (1.0 - config.rate_mix) + config.rate_mix * activity_ratio
    spatial = (1.0 - config.spatial_mix) * long_spatial + config.spatial_mix * recent_spatial
    return base_total * rate_multiplier * spatial


def poisson_scores(expected: np.ndarray, observed: np.ndarray) -> dict[str, float | bool]:
    """Score expected counts using the common rolling benchmark metrics."""
    lam = np.maximum(np.asarray(expected, dtype=float), EPSILON)
    observed = np.asarray(observed, dtype=float)
    total_expected = float(lam.sum())
    total_observed = float(observed.sum())
    log_likelihood = float((observed * np.log(lam) - lam - gammaln(observed + 1.0)).sum())
    uniform_lam = np.full_like(lam, total_expected / lam.size)
    uniform_ll = float((observed * np.log(uniform_lam) - uniform_lam - gammaln(observed + 1.0)).sum())
    probability = 1.0 - np.exp(-lam)
    observed_binary = (observed.ravel() >= 1).astype(float)
    brier = float(np.mean((probability.ravel() - observed_binary) ** 2))
    labels = observed_binary
    positive = labels == 1
    negative = labels == 0
    if positive.any() and negative.any():
        from scipy.stats import rankdata

        ranks = rankdata(probability.ravel(), method="average")
        positive_count = int(positive.sum())
        negative_count = int(negative.sum())
        positive_rank_sum = float(ranks[positive].sum())
        auc = float(
            (positive_rank_sum - positive_count * (positive_count + 1) / 2)
            / (positive_count * negative_count)
        )
    else:
        auc = float("nan")
    from scipy.stats import poisson

    lower = float(poisson.ppf(0.025, total_expected))
    upper = float(poisson.ppf(0.975, total_expected))
    return {
        "expected_events": total_expected,
        "observed_events": total_observed,
        "log_likelihood": log_likelihood,
        "uniform_log_likelihood": uniform_ll,
        "information_gain_per_event": (log_likelihood - uniform_ll) / total_observed if total_observed else float("nan"),
        "n_test_lower": lower,
        "n_test_upper": upper,
        "n_test_pass": bool(lower <= total_observed <= upper),
        "roc_auc": auc,
        "brier_score": brier,
    }
