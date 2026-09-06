# Methods

## Magnitude of Completeness

The magnitude of completeness, Mc, is estimated from the non-cumulative frequency-magnitude distribution. Events are binned at 0.1 magnitude units, and the maximum curvature point is defined as the magnitude bin with the largest event count. A conservative offset of +0.2 magnitude units is then added:

```
Mc = M_max_curvature + 0.2
```

All downstream steps use only events with magnitude greater than or equal to this final Mc. The script also saves a figure showing the histogram, the maximum-curvature magnitude, and the final Mc threshold.

## Declustering

The project applies the Gardner-Knopoff (1974) window method as a first-order declustering procedure. Events are processed from largest to smallest magnitude. For each larger event, smaller subsequent events inside its magnitude-dependent time and distance window are marked as dependent events.

The lookup table used here is:

| Magnitude range | Time window | Distance window |
|---|---:|---:|
| M < 2.5 | 6 days | 15 km |
| 2.5 <= M < 3.5 | 6 days | 15 km |
| 3.5 <= M < 4.5 | 11 days | 20 km |
| 4.5 <= M < 5.5 | 22 days | 30 km |
| 5.5 <= M < 6.5 | 42 days | 50 km |
| 6.5 <= M < 7.5 | 83 days | 70 km |
| M >= 7.5 | 155 days | 100 km |

Distances use local degree approximations: 1 degree latitude is approximately 111 km, and 1 degree longitude is approximately 111 cos(latitude) km.

## ETAS Model

The Epidemic-Type Aftershock Sequence model represents earthquake occurrence as a conditional point process. In a space-time ETAS model, the conditional intensity is:

```
lambda(t, x, y) = mu(x, y) + sum_i g(t - t_i, x - x_i, y - y_i, M_i)
```

Where:

- **mu** — background seismicity rate (events/day per square degree)
- **g** — triggering function with:
  - **Productivity:** `A * exp(alpha * (M_i - M0))` — larger quakes trigger more aftershocks
  - **Temporal decay (Omori):** `(t - t_i + c)^(-p)` — aftershocks decay as a power law
  - **Spatial decay:** `(1 + r^2 / D * exp(gamma * (M_i - M0)))^(-q)` — spatial clustering

The 8 parameters (mu, A, c, alpha, p, D, q, gamma) are estimated by maximum likelihood using the CRAN `ETAS` package in R.

For the 72-hour forecast grid, the Python fallback evaluates the fitted conditional intensity on a 0.1-degree grid, integrates over three days, and converts expected counts to Poisson exceedance probabilities:

```
P(N >= 1) = 1 - exp(-expected_count)
```

## Calibration

The raw ETAS forecast may not match the observed seismicity rate if the fitted parameters are not calibrated for the target region. The calibration script (`src/calibrate_etas.py`) computes the ratio of the observed historical daily earthquake rate to the ETAS-predicted rate, and scales the background rate parameter (mu) accordingly:

```
scale = observed_daily_rate * 3 / ETAS_predicted_72h
mu_calibrated = mu_raw * scale
```

This preserves the spatial triggering pattern from the ETAS fit while correcting the total expected count to match observations.

## Validation

The validation script (`src/06_validate.py`) compares forecast probabilities against observed earthquakes using standard seismological skill scores:

### N-test (Number test)
Compares the total number of observed earthquakes against the 95% Poisson confidence interval of the model-predicted count. PASS means the model correctly predicts the total event rate.

### Log-likelihood
The Poisson log-likelihood measures how well the forecast probabilities match the observed binary outcomes (earthquake / no earthquake in each cell). Higher values indicate a better fit.

### Information gain per event
The difference between the model's log-likelihood and a uniform baseline, divided by the number of observed events. Positive values mean the model outperforms uniform random.

### ROC curve and AUC
The Receiver Operating Characteristic curve plots hit rate vs false alarm rate across all probability thresholds. AUC = 0.5 is random, AUC = 1.0 is perfect.

### Reliability diagram
Plots predicted probability against observed frequency. Points near the diagonal indicate well-calibrated probabilities.

## Simple baseline

The historical-rate baseline uses a Gaussian-smoothed histogram of earthquake locations with sigma = 0.4 degrees. It has no short-term triggering component and is a reference model, not a claim of operational reliability. The rolling audit also compares it with a uniform-rate model to separate spatial ranking from total-rate behavior.

## Rolling-origin baseline audit

`src/08_rolling_backtest.py` evaluates two transparent reference models without using the R ETAS fit:

- **Historical rate:** Gaussian-smoothed spatial counts from events with `datetime <= origin`, divided by the training span and projected over the 72-hour horizon.
- **Uniform rate:** the same training-only total event rate distributed evenly across all forecast cells.

Origins are deterministic, spaced every three days, and the default horizon is three days, so windows do not overlap. Observations are selected strictly after the origin and at or before the forecast end. The forecast grid has 60×60 cell values and 61×61 coordinate edges. Per-window rows are retained so aggregate scores do not hide unstable windows. The report includes Poisson likelihood, information gain relative to the per-window uniform rate, N-test intervals, ROC AUC, Brier score, and pooled reliability bins.

This audit is a baseline reference, not a rolling ETAS evaluation. A true ETAS backtest must refit parameters at every origin using only prior data and use an origin-safe calibration rule.

## External research basis

The research design follows the principles summarized in [`docs/RESEARCH.md`](docs/RESEARCH.md): controlled and reproducible testing from [CSEP](https://cseptesting.org/), standardized forecast evaluation and data models from [pyCSEP](https://doi.org/10.21105/joss.03658), prospective benchmark practice from the [California forecast database](https://www.nature.com/articles/s41597-025-05766-3), and ETAS completeness findings from [Mizrahi et al.](https://arxiv.org/html/2105.00888v3).

- **Single regional Mc:** one Mc value is estimated for the whole offshore Tohoku/Kanto region, although completeness may vary in space and time.
- **First-order declustering:** Gardner-Knopoff windows are simple and reproducible, but they do not estimate uncertainty and can misclassify events.
- **Rolling ETAS not yet implemented:** the new multi-window audit evaluates simple baselines only; the ETAS parameters are not refit at each origin.
- **Calibration leakage in legacy example:** the checked-in calibrated ETAS map uses the full catalog rate and is retained as an illustrative comparison, not a prospective result.
- **Grid implementations:** the legacy 61×61 visualization path and the new 60×60 cell audit have different contracts; each artifact's metadata and method documentation must be followed.
- **ETAS convergence:** the CRAN ETAS package may require many iterations (300+) for full convergence, especially with a small mainshock catalog.
