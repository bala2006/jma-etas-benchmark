# Optional R forecast wrapper.
#
# The CRAN ETAS package has changed prediction helper interfaces across
# versions. For reproducibility, this repository uses Python as the guaranteed
# forecast path. This script attempts to load the fitted model and reports a
# clear message; if no compatible predict method is available, run
# python src/04_etas_forecast.py.

args <- commandArgs(trailingOnly = FALSE)
file_arg <- "--file="
script_path <- sub(file_arg, "", args[grep(file_arg, args)])
if (length(script_path) == 0) {
  script_path <- file.path("etas", "generate_forecast.R")
}
root <- normalizePath(file.path(dirname(script_path), ".."), winslash = "/", mustWork = TRUE)
model_path <- file.path(root, "data", "outputs", "etas_fit.rds")

if (!file.exists(model_path)) {
  stop("Missing ETAS model. Run Rscript etas/fit_etas.R first: ", model_path)
}

message("Loading ETAS model: ", model_path)
fit <- readRDS(model_path)

if ("predict" %in% methods(class = class(fit))) {
  message("A predict method appears to be available for this ETAS object.")
  message("This repository still uses the Python fallback for stable grid output.")
} else {
  message("No version-stable R prediction interface detected for this ETAS object.")
  message("Use the robust fallback: python src/04_etas_forecast.py")
}

quit(status = 0)
