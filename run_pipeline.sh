#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  JMA-ETAS-BENCHMARK — Full Pipeline"
echo "============================================"

echo ""
echo "Step 1/10: Load and spatially filter the raw JMA catalog"
echo "  Input:  data/raw/jma_tohoku_2010_2023.csv"
echo "  Output: data/processed/catalog_region_2010_2023.csv"
python3 src/01_load_and_filter.py

echo ""
echo "Step 2/10: Estimate magnitude of completeness Mc"
echo "  Input:  data/processed/catalog_region_2010_2023.csv"
echo "  Output: data/processed/catalog_mc_filtered.csv"
echo "          data/outputs/mc_estimate.txt"
echo "          figures/fmd_mc_estimate.png"
python3 src/02_estimate_mc.py

echo ""
echo "Step 3/10: Gardner-Knopoff declustering"
echo "  Input:  data/processed/catalog_mc_filtered.csv"
echo "  Output: data/processed/mainshocks_gk.csv"
echo "          data/processed/aftershocks_gk.csv"
python3 src/03_decluster.py

echo ""
echo "Step 4/10: Fit ETAS model in R"
export ETAS_MIN_MAG="${ETAS_MIN_MAG:-4.0}"
export ETAS_NO_ITR="${ETAS_NO_ITR:-11}"
export ETAS_VERBOSE="${ETAS_VERBOSE:-false}"
export ETAS_SEED="${ETAS_SEED:-1}"
export ETAS_MAX_ATTEMPTS="${ETAS_MAX_ATTEMPTS:-5}"
echo "  Input:  data/processed/mainshocks_gk.csv"
echo "  Output: data/outputs/etas_parameters.csv"
echo "          data/outputs/etas_fit.rds"
echo "  Controls: ETAS_MIN_MAG=${ETAS_MIN_MAG}, ETAS_NO_ITR=${ETAS_NO_ITR}"
echo "  (Override with env vars for longer scientific fit, e.g. ETAS_NO_ITR=300)"
Rscript etas/fit_etas.R

echo ""
echo "Step 5/10: Generate ETAS 72-hour forecast grid"
echo "  Input:  data/processed/mainshocks_gk.csv"
echo "          data/outputs/etas_parameters.csv"
echo "  Output: data/outputs/forecast_prob_72h.npy"
echo "          data/outputs/forecast_grid_metadata.json"
python3 src/04_etas_forecast.py

echo ""
echo "Step 6/10: Calibrate ETAS forecast to match observed seismicity rate"
echo "  Input:  data/outputs/forecast_prob_72h.npy"
echo "          data/processed/catalog_mc_filtered.csv"
echo "  Output: data/outputs/etas_calibrated_prob_72h.npy"
echo "          data/outputs/etas_parameters_calibrated.csv"
python3 src/calibrate_etas.py

echo ""
echo "Step 7/10: Generate simple historical-rate baseline forecast"
echo "  Input:  data/processed/catalog_mc_filtered.csv"
echo "  Output: data/outputs/simple_prob_72h.npy"
python3 src/07_simple_forecast.py

echo ""
echo "Step 8/10: Visualize the ETAS forecast map"
echo "  Input:  data/outputs/forecast_prob_72h.npy"
echo "  Output: figures/forecast_map_72h.png"
python3 src/05_visualize.py

echo ""
echo "Step 9/10: Validate the example forecasts against observed data"
echo "  Input:  data/outputs/*_prob_72h.npy"
echo "          data/processed/catalog_mc_filtered.csv"
echo "  Output: data/outputs/validation_metrics.txt"
echo "          figures/validation_roc.png"
echo "          figures/validation_reliability.png"
echo "          figures/validation_forecast_vs_observed.png"
python3 src/06_validate.py

echo ""
echo "Step 10/11: Run the training-only rolling baseline audit"
echo "  Input:  data/processed/catalog_mc_filtered.csv"
echo "  Output: data/outputs/rolling_backtest_windows.csv"
echo "          data/outputs/rolling_backtest_summary.json"
echo "          data/outputs/rolling_backtest_reliability.json"
echo "          figures/rolling_backtest.png"
python3 src/08_rolling_backtest.py

echo ""
echo "Step 11/11: Train ASTR and evaluate the untouched 2022-2023 test block"
echo "  Input:  data/processed/catalog_mc_filtered.csv"
echo "  Output: data/outputs/adaptive_model_config.json"
echo "          data/outputs/adaptive_model_tuning.csv"
echo "          data/outputs/adaptive_backtest_windows.csv"
echo "          data/outputs/adaptive_backtest_summary.json"
echo "          figures/adaptive_backtest.png"
python3 src/09_train_adaptive_model.py

echo ""
echo "============================================"
echo "  Pipeline complete!"
echo "  Results: data/outputs/"
echo "  Figures: figures/"
echo "============================================"
