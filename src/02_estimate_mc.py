"""Estimate magnitude of completeness using maximum curvature plus 0.2.

Maximum curvature chooses the magnitude bin with the largest non-cumulative
frequency in the frequency-magnitude distribution. The +0.2 offset is a simple
conservative correction often used to avoid keeping events below the practical
reporting threshold.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
IN_CATALOG = ROOT / "data" / "processed" / "catalog_region_2010_2023.csv"
OUT_CATALOG = ROOT / "data" / "processed" / "catalog_mc_filtered.csv"
OUT_MC = ROOT / "data" / "outputs" / "mc_estimate.txt"
OUT_FIG = ROOT / "figures" / "fmd_mc_estimate.png"
BIN_WIDTH = 0.1
MC_OFFSET = 0.2


def estimate_mc(magnitudes: pd.Series) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Return final Mc, max-curvature magnitude, bin centers, and counts."""
    mags = magnitudes.dropna().to_numpy()
    if len(mags) == 0:
        raise ValueError("No magnitudes available for Mc estimation.")

    min_mag = np.floor(mags.min() / BIN_WIDTH) * BIN_WIDTH
    max_mag = np.ceil(mags.max() / BIN_WIDTH) * BIN_WIDTH
    bins = np.arange(min_mag, max_mag + BIN_WIDTH * 1.5, BIN_WIDTH)
    counts, edges = np.histogram(mags, bins=bins)
    centers = edges[:-1] + BIN_WIDTH / 2.0

    if counts.max() == 0:
        raise ValueError("Magnitude histogram is empty; cannot estimate Mc.")

    mc_curvature = round(float(centers[np.argmax(counts)]), 1)
    mc_final = round(mc_curvature + MC_OFFSET, 1)
    return mc_final, mc_curvature, centers, counts


def main() -> None:
    """Estimate Mc, save the filtered catalog, and create the FMD figure."""
    if not IN_CATALOG.exists():
        raise FileNotFoundError(f"Run src/01_load_and_filter.py first. Missing: {IN_CATALOG}")

    print(f"Reading filtered regional catalog: {IN_CATALOG}")
    df = pd.read_csv(IN_CATALOG, parse_dates=["datetime"])
    mc_final, mc_curvature, centers, counts = estimate_mc(df["magnitude"])

    filtered = df[df["magnitude"] >= mc_final].copy().reset_index(drop=True)
    filtered.to_csv(OUT_CATALOG, index=False)

    OUT_MC.parent.mkdir(parents=True, exist_ok=True)
    OUT_MC.write_text(
        f"Mc_max_curvature={mc_curvature:.1f}\nMc_conservative={mc_final:.1f}\n"
        f"bin_width={BIN_WIDTH:.1f}\noffset={MC_OFFSET:.1f}\n"
        f"events_before={len(df)}\nevents_after={len(filtered)}\n",
        encoding="utf-8",
    )

    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5), dpi=160)
    ax.bar(centers, counts, width=BIN_WIDTH * 0.9, color="#4a5568", edgecolor="white")
    ax.axvline(mc_curvature, color="#2b6cb0", linestyle="--", linewidth=2, label=f"Max curvature = {mc_curvature:.1f}")
    ax.axvline(mc_final, color="#c53030", linestyle="-", linewidth=2, label=f"Final Mc = {mc_final:.1f}")
    ax.set_xlabel("Magnitude")
    ax.set_ylabel("Non-cumulative event count")
    ax.set_title("Magnitude of Completeness from JMA Catalog")
    ax.legend(frameon=False)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_FIG)
    plt.close(fig)

    print(f"Estimated Mc={mc_final:.1f} using max curvature {mc_curvature:.1f} + {MC_OFFSET:.1f}.")
    print(f"Saved Mc-filtered catalog with {len(filtered)} events to {OUT_CATALOG}")
    print(f"Saved FMD figure to {OUT_FIG}")


if __name__ == "__main__":
    main()
