# Research basis and implementation findings

This page records the external research that shaped the current improvement and separates published practice from what this repository actually demonstrates.

## What the research says

### 1. Forecasting needs controlled, comparative testing

The [Collaboratory for the Study of Earthquake Predictability (CSEP)](https://cseptesting.org/) describes earthquake forecasting as an international effort built around rigorous model evaluation and forecast-testing experiments. Its stated objectives include shared testing procedures, community standards, controlled experiments, and access to common data products.

**Design consequence for this repository:** a forecast map is not enough. The project needs fixed forecast definitions, explicit training and scoring periods, reference models, machine-readable outputs, and aggregate results across many origins.

### 2. Prospective or pseudo-prospective evaluation protects against leakage

The [CSEP benchmark database of ten years of prospective next-day forecasts](https://www.nature.com/articles/s41597-025-05766-3) emphasizes fully prospective evaluation against future seismicity, fair and reproducible procedures, controlled data access, and transparent forecast artifacts. The study describes more than 50,000 daily forecasts produced under a common experiment rather than judging a model from one selected window.

**Design consequence:** every new rolling-audit forecast is trained using events with `datetime <= origin`, while only events with `origin < datetime <= origin + horizon` are scored. Non-overlapping three-day origins make the first result easier to interpret and avoid silently treating overlapping windows as independent.

### 3. Standardized tooling improves comparability

The [pyCSEP toolkit paper](https://doi.org/10.21105/joss.03658) presents a Python toolkit for earthquake-catalog processing, forecast data models, statistical tests, and visualization. The goal is to make forecast development and comparison easier through defined formats and community-oriented evaluation implementations.

**Design consequence:** the new audit writes a long-form per-window CSV, a JSON summary, a JSON reliability table, and a figure. It also makes the grid contract explicit: 60×60 cell values use 61×61 coordinate edges. This prevents edge coordinates from being counted as extra cells.

### 4. ETAS results depend on catalog completeness

[Mizrahi, Nandan, and Wiemer’s ETAS incompleteness study](https://arxiv.org/html/2105.00888v3) describes pseudo-prospective experiments and shows why long-term and short-term detection incompleteness can bias ETAS calibration and forecasting. Their results motivate modeling or correcting completeness rather than assuming that a single constant threshold captures every period equally well.

**Design consequence:** the repository still uses a simple global Mc=0.8 threshold, but now labels that assumption as a limitation. The rolling baseline result is not presented as a completed ETAS solution; a future ETAS refit should use an origin-specific cutoff and investigate time-varying completeness.

## Findings from the repository

The original checked-in benchmark exposed four concrete reliability problems:

1. **One-window validation:** the ETAS comparison used one 72-hour window with five events, so its AUC and reliability plot were too unstable for a general performance claim.
2. **Information leakage in calibration:** the calibration used the full filtered catalog, including information from the scored period, so it was not prospective.
3. **Ambiguous forecast origin:** the Python evaluator derived its origin from the latest event while the R fit used a broader date range. The resulting train/test boundary was not explicit.
4. **Grid contract ambiguity:** the stored 61×61 arrays were treated partly as cell values and partly as coordinate edges, while observations occupied only 60×60 cells.

## Implemented improvement

`src/08_rolling_backtest.py` is a focused, safe improvement rather than a claim of a finished operational ETAS system.

| Property | Implementation |
|---|---|
| Forecast schedule | 730 deterministic origins, every 3 days, 2018-01-01 through 2023-12-28 |
| Horizon | 72 hours |
| Training boundary | Events at or before each origin |
| Scoring boundary | Events after each origin and at or before origin + 72 hours |
| Overlap | None; step equals horizon |
| Models | Training-only Gaussian-smoothed historical rate and training-only uniform rate |
| Grid | 60×60 cells with 61×61 edges at 0.1° spacing |
| Outputs | Per-window likelihood/count/AUC/Brier rows, aggregate summary, pooled reliability, diagnostic figure |
| ETAS status | Not refit in this audit; no rolling ETAS skill claim |

## Results from the new rolling audit

The audit was run on the checked-in Mc-filtered catalog (5,608 events). It provides a multi-window reference that is safer to interpret than the original five-event snapshot:

| Model | Origins | Total expected | Total observed | Aggregate N-test | Window N-test pass rate | Mean AUC | Mean IG/event |
|---|---:|---:|---:|:---:|---:|---:|---:|
| Historical-rate baseline | 730 | 2,332.79 | 2,661 | **FAIL** | 94.9% | 0.515 | -0.0528 |
| Uniform-rate baseline | 730 | 2,341.36 | 2,661 | **FAIL** | 94.9% | 0.500 | ~0.0000 |

The spatial historical-rate baseline has a modest mean AUC above 0.5, but its fifth-percentile AUC is 0.167 and its information gain relative to the per-window uniform reference is negative. Both models underpredict the aggregate count by roughly 12–14%. The result suggests that long-term spatial smoothing alone is not a reliable short-horizon rate model for this catalog, and that temporal variation, short-term triggering, or completeness effects need to be modeled.

![Rolling-origin baseline audit](../figures/rolling_backtest.png)

The machine-readable source is [`../data/outputs/rolling_backtest_summary.json`](../data/outputs/rolling_backtest_summary.json), with per-window rows in [`../data/outputs/rolling_backtest_windows.csv`](../data/outputs/rolling_backtest_windows.csv).

## What this does not establish

- It does not establish operational ETAS performance because the R ETAS model is not refit at every origin.
- It does not estimate parameter uncertainty or uncertainty from the catalog completeness threshold.
- It does not convert a research benchmark into an alerting or emergency-response system.
- It does not prove that spatial AUC above 0.5 will generalize to another period, region, or magnitude threshold.

## Next research milestone

The next scientifically important implementation is a true rolling ETAS experiment:

1. choose origins before inspecting their future windows;
2. pass each origin as a precise timestamp to the R fitting stage;
3. fit parameters using only catalog data available by that origin;
4. use a training-only calibration rule or fit the rate jointly;
5. generate a forecast with the same 60×60 cell contract;
6. score ETAS and baselines with identical windows and metrics;
7. report per-window results, aggregate likelihood, N-test behavior, calibration, and uncertainty intervals.

The current repository now has the baseline schedule, output schema, and reporting pattern needed for that extension.

## Sources

- [CSEP Testing](https://cseptesting.org/) — community goals and rigorous earthquake-forecast testing.
- [pyCSEP: A Python Toolkit For Earthquake Forecast Developers](https://doi.org/10.21105/joss.03658) — forecast data models, evaluation tests, and visualization tooling.
- [A benchmark database of ten years of prospective next-day earthquake forecasts in California](https://www.nature.com/articles/s41597-025-05766-3) — prospective, controlled, reproducible benchmark practice.
- [Embracing Data Incompleteness for Better Earthquake Forecasting](https://arxiv.org/html/2105.00888v3) — ETAS calibration, incompleteness, and pseudo-prospective forecasting.
- [JMA Unified Hypocenter Catalog](https://www.data.jma.go.jp/svd/eqev/data/bulletin/hypo_e.html) — project data source.

Content was rephrased for compliance with licensing restrictions.


## Trainable model added after the baseline audit

The next experiment is implemented in `src/09_train_adaptive_model.py` and `src/adaptive_spatiotemporal.py`. It is called **Adaptive Spatiotemporal Rate (ASTR)**.

ASTR was chosen because it is:

- causal at every origin;
- interpretable without a black-box machine-learning dependency;
- computationally reproducible with the repository's Python requirements;
- directly comparable on the same spatial cells and Poisson scoring rules;
- capable of incorporating short-term activity without mislabeling itself as full ETAS.

The training protocol freezes the final test block before tuning:

| Stage | Origins | Purpose |
|---|---:|---|
| Validation | 487 from 2018–2021 | Select ASTR hyperparameters |
| Tuning subset | 122 validation origins | Reduce repeated computation while preserving chronological coverage |
| Final test | 243 from 2022–2023 | Untouched comparison |

Every model receives only events with `datetime <= origin`. Future observations are used only for scoring. The final test uses identical 72-hour windows and a 60×60 cell grid for the uniform, historical-rate, and ASTR models.

## ASTR result

| Model | Test total log likelihood | Mean AUC | Aggregate N-test |
|---|---:|---:|:---:|
| Uniform rate | -5,352.62 | 0.500 | FAIL |
| Historical rate | -5,408.81 | 0.498 | FAIL |
| ASTR | **-5,385.80** | **0.527** | **PASS** |

ASTR improves on historical rate by 23.01 total log-likelihood units and 0.028 mean AUC points on the untouched test block. It does not beat the uniform-rate baseline on total likelihood. Therefore the result supports a **partial, local improvement**, not a state-of-the-art or operational claim.

The model's selected parameters include a 90-day recent window, 14-day decay time, 0.25 spatial mixture, and 0.75 activity-rate mixture. Its selected magnitude gain is 0.0; validation did not support adding magnitude weighting under this protocol. That parameter must remain frozen for the final test rather than being changed after seeing the result.

## Real-world benchmark boundary

The [USGS Aftershock Forecast Overview](https://earthquake.usgs.gov/data/oaf/overview.php) describes a continuously updated aftershock forecasting process that incorporates the behavior of the specific sequence as it develops. The [USGS scientific background](https://earthquake.usgs.gov/data/oaf/background.php) describes the ETAS-related productivity and temporal-decay parameters used in aftershock forecasting. These sources establish the relevant operational model family.

This repository does not claim to beat USGS OAF or UCERF3-ETAS directly. A valid comparison would require the same catalog, region, target magnitude range, forecast horizon, forecast grid, data cutoffs, and prospective evaluation. The current result is a fair comparison against local reproducible baselines, while the next research step is an origin-safe rolling ETAS refit.

Content was rephrased for compliance with licensing restrictions.
