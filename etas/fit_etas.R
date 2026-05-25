# Fit a space-time ETAS model using the CRAN ETAS package.
#
# This script is intentionally verbose because the expected reader may be more
# comfortable with Python than R. The output parameters are also written to CSV
# so that the Python forecast fallback can run even when ETAS prediction helper
# functions differ across package versions.

suppressPackageStartupMessages({
  if (!requireNamespace("ETAS", quietly = TRUE)) {
    stop("The R package 'ETAS' is not installed. In Docker it is installed during image build.")
  }
})

args <- commandArgs(trailingOnly = FALSE)
file_arg <- "--file="
script_path <- sub(file_arg, "", args[grep(file_arg, args)])
if (length(script_path) == 0) {
  script_path <- file.path("etas", "fit_etas.R")
}
root <- normalizePath(file.path(dirname(script_path), ".."), winslash = "/", mustWork = TRUE)
mainshock_path <- file.path(root, "data", "processed", "mainshocks_gk.csv")
param_path <- file.path(root, "data", "outputs", "etas_parameters.csv")
model_path <- file.path(root, "data", "outputs", "etas_fit.rds")
fit_input_path <- file.path(root, "data", "outputs", "etas_fit_input.csv")
fit_metadata_path <- file.path(root, "data", "outputs", "etas_fit_metadata.txt")

env_value <- function(name, default = "") {
  value <- Sys.getenv(name, unset = default)
  if (is.na(value) || value == "") default else value
}

env_numeric <- function(name, default) {
  value <- suppressWarnings(as.numeric(Sys.getenv(name, unset = "")))
  if (is.na(value)) default else value
}

env_integer <- function(name, default) {
  value <- suppressWarnings(as.integer(Sys.getenv(name, unset = "")))
  if (is.na(value)) default else value
}

env_logical <- function(name, default) {
  value <- tolower(Sys.getenv(name, unset = ""))
  if (value %in% c("1", "true", "yes", "y")) return(TRUE)
  if (value %in% c("0", "false", "no", "n")) return(FALSE)
  default
}

if (!file.exists(mainshock_path)) {
  stop("Missing mainshock catalog. Run python src/03_decluster.py first: ", mainshock_path)
}

message("Reading mainshock catalog: ", mainshock_path)
catalog <- read.csv(mainshock_path, stringsAsFactors = FALSE)
catalog$datetime <- as.POSIXct(catalog$datetime, tz = "UTC")
catalog <- catalog[order(catalog$datetime), ]

fit_start <- env_value("ETAS_FIT_START", "2010-01-01")
fit_end <- env_value("ETAS_FIT_END", "2023-12-31")
min_mag <- env_numeric("ETAS_MIN_MAG", min(catalog$magnitude, na.rm = TRUE))
max_events <- env_integer("ETAS_MAX_EVENTS", 0)
no_itr <- env_integer("ETAS_NO_ITR", 11)
verbose_fit <- env_logical("ETAS_VERBOSE", FALSE)
base_seed <- env_integer("ETAS_SEED", 1)
max_attempts <- env_integer("ETAS_MAX_ATTEMPTS", 5)
set.seed(base_seed)

fit_start_time <- as.POSIXct(fit_start, tz = "UTC")
fit_end_time <- as.POSIXct(paste(fit_end, "23:59:59"), tz = "UTC")
catalog_before_filter <- nrow(catalog)
catalog <- catalog[
  catalog$datetime >= fit_start_time &
    catalog$datetime <= fit_end_time &
    catalog$magnitude >= min_mag,
]
if (max_events > 0 && nrow(catalog) > max_events) {
  catalog <- tail(catalog, max_events)
  fit_start <- format(min(catalog$datetime), "%Y-%m-%d")
}
if (nrow(catalog) < 20) {
  stop("ETAS fit subset has too few events after filters: ", nrow(catalog))
}

message(
  "ETAS fit subset: ", nrow(catalog), " of ", catalog_before_filter,
  " mainshocks; start=", fit_start,
  "; end=", fit_end,
  "; min_mag=", min_mag,
  "; max_events=", max_events,
  "; no_itr=", no_itr,
  "; seed=", base_seed,
  "; max_attempts=", max_attempts
)
write.csv(catalog, fit_input_path, row.names = FALSE)

# The ETAS package expects longitude/latitude-like spatial coordinates and time
# in days. The study region is passed as a simple closed polygon.
study_region <- list(
  long = c(140.0, 146.0, 146.0, 140.0),
  lat = c(36.0, 36.0, 42.0, 42.0)
)

# ETAS::catalog expects a data.frame with date, time, longitude, latitude,
# magnitude, and optionally depth. Keep the data in calendar time here; ETAS
# converts it internally to numeric decimal days.
etas_data <- data.frame(
  date = format(catalog$datetime, "%Y-%m-%d"),
  time = format(catalog$datetime, "%H:%M:%S"),
  long = catalog$longitude,
  lat = catalog$latitude,
  mag = catalog$magnitude,
  depth = catalog$depth_km
)

catalog_formals <- names(formals(ETAS::catalog))
catalog_args <- list(
  data = etas_data,
  time.begin = fit_start,
  study.start = fit_start,
  study.end = fit_end,
  region.poly = study_region,
  mag.threshold = min(etas_data$mag)
)
if ("dist.unit" %in% catalog_formals) {
  catalog_args$dist.unit <- "degree"
}
if ("tz" %in% catalog_formals) {
  catalog_args$tz <- "GMT"
}
etas_catalog <- do.call(ETAS::catalog, catalog_args)

# Parameter order used by current CRAN ETAS:
# mu, A, c, alpha, p, D, q, gamma.
param0 <- c(
  mu = 0.59,
  A = 0.20,
  c = 0.023,
  alpha = 1.50,
  p = 1.11,
  D = 0.0012,
  q = 1.86,
  gamma = 1.04
)

message("Fitting ETAS model on one CPU thread.")

etas_formals <- names(formals(ETAS::etas))
fit_args <- list(
  object = etas_catalog,
  param0 = param0,
  verbose = verbose_fit,
  plot.it = FALSE,
  no.itr = no_itr
)
if ("nthreads" %in% etas_formals) {
  fit_args$nthreads <- 1
}
if ("ncpp" %in% etas_formals) {
  fit_args$ncpp <- 1
}

fit <- NULL
fit_error <- NULL
for (attempt in seq_len(max_attempts)) {
  attempt_seed <- base_seed + attempt - 1
  set.seed(attempt_seed)
  message("ETAS fit attempt ", attempt, "/", max_attempts, " with seed ", attempt_seed)
  fit <- tryCatch(
    do.call(ETAS::etas, fit_args),
    error = function(exc) {
      fit_error <<- exc
      NULL
    }
  )
  if (!is.null(fit)) {
    break
  }
  message("ETAS fit attempt failed: ", conditionMessage(fit_error))
}
if (is.null(fit)) {
  stop("ETAS fit failed after ", max_attempts, " attempts. Last error: ", conditionMessage(fit_error))
}

message("ETAS fit complete.")
saveRDS(fit, model_path)
message("Saved full R model to: ", model_path)

# Extract fitted parameters robustly from common object layouts. If a package
# version exposes parameters under a different name, keep the full model in RDS
# and fall back to empirical defaults for the CSV instead of crashing.
extract_params <- function(model) {
  candidates <- list(model$param, model$params, model$par, model$estimate, model$coefficients)
  for (candidate in candidates) {
    if (!is.null(candidate) && length(candidate) > 0) {
      values <- as.numeric(candidate)
      names(values) <- names(candidate)
      return(values)
    }
  }
  numeric_parts <- unlist(model)
  numeric_parts <- numeric_parts[is.finite(suppressWarnings(as.numeric(numeric_parts)))]
  if (length(numeric_parts) > 0) {
    values <- as.numeric(numeric_parts[seq_len(min(length(numeric_parts), 8))])
    names(values) <- names(numeric_parts)[seq_len(length(values))]
    return(values)
  }
  return(numeric(0))
}

params <- extract_params(fit)
if (length(params) == 0 || any(is.na(names(params))) || any(names(params) == "")) {
  warning("Could not reliably extract named ETAS parameters. Assigning standard ETAS names by position.")
  if (length(params) >= 8) {
    params <- params[1:8]
    names(params) <- c("mu", "A", "c", "alpha", "p", "D", "q", "gamma")
  } else {
    params <- c(mu = 1e-4, A = 0.01, c = 0.01, alpha = 1.0, p = 1.1, D = 0.05, q = 1.8, gamma = 0.5)
  }
}

# Normalize several common parameter names for the Python fallback.
name_map <- c(k0 = "A", K = "A", k = "A", b = "alpha", d = "D", q = "D")
for (old_name in names(name_map)) {
  if (old_name %in% names(params) && !(name_map[[old_name]] %in% names(params))) {
    names(params)[names(params) == old_name] <- name_map[[old_name]]
  }
}

param_df <- data.frame(parameter = names(params), value = as.numeric(params))
write.csv(param_df, param_path, row.names = FALSE)
writeLines(
  c(
    paste0("input_events_before_filter=", catalog_before_filter),
    paste0("input_events_fit=", nrow(catalog)),
    paste0("fit_start=", fit_start),
    paste0("fit_end=", fit_end),
    paste0("min_mag=", min_mag),
    paste0("max_events=", max_events),
    paste0("no_itr=", no_itr),
    paste0("seed=", base_seed),
    paste0("max_attempts=", max_attempts),
    paste0("verbose=", verbose_fit),
    paste0("fit_input=", fit_input_path)
  ),
  fit_metadata_path
)

message("Parameter estimates:")
print(param_df)
message("Saved parameter table to: ", param_path)
message("Saved fit metadata to: ", fit_metadata_path)
