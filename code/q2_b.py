"""Implement the five inference methods required for Question 2b."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_OUTPUT = Path("output/q2b_standard_errors.tex")
REQUIRED_COLUMNS = {"YEAR", "MONTH", "dp", "rf", "re"}
MONTHS_PER_YEAR = 12
FIXED_BANDWIDTH = 11


@dataclass(frozen=True)
class InferenceResult:
    """Slope inference for one covariance estimator."""

    method: str
    slope: float
    standard_error: float
    t_statistic: float


def validate_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate, date, and chronologically sort the monthly equity dataset."""
    missing_columns = REQUIRED_COLUMNS.difference(data.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"EQ Dataset is missing required columns: {missing}")

    validated = data.copy()
    for column in REQUIRED_COLUMNS:
        validated[column] = pd.to_numeric(validated[column], errors="raise")
    if validated[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError("Required EQ Dataset columns contain missing values.")
    if not np.equal(validated["YEAR"], np.floor(validated["YEAR"])).all():
        raise ValueError("YEAR must be integer-valued.")
    if not np.equal(validated["MONTH"], np.floor(validated["MONTH"])).all():
        raise ValueError("MONTH must be integer-valued.")

    validated["YEAR"] = validated["YEAR"].astype("int64")
    validated["MONTH"] = validated["MONTH"].astype("int64")
    if not validated["MONTH"].between(1, 12).all():
        raise ValueError("MONTH must be between 1 and 12.")
    validated["date"] = pd.to_datetime(
        {"year": validated["YEAR"], "month": validated["MONTH"], "day": 1},
        errors="raise",
    )
    if validated["date"].duplicated().any():
        raise ValueError("EQ Dataset contains duplicate monthly observations.")
    validated = validated.sort_values("date", kind="stable").reset_index(drop=True)

    observed = pd.PeriodIndex(validated["date"], freq="M")
    expected = pd.period_range(observed.min(), observed.max(), freq="M")
    if not observed.equals(expected):
        missing = expected.difference(observed)
        examples = [str(period) for period in missing[:5]]
        raise ValueError(f"EQ Dataset has missing months: {examples}")

    validated["dividend_price"] = np.exp(validated["dp"])
    validated["excess_return"] = np.exp(validated["re"]) - np.exp(
        validated["rf"]
    )
    if not np.isfinite(
        validated[["dividend_price", "excess_return"]].to_numpy()
    ).all():
        raise ValueError("Constructed D/P or xRe contains non-finite values.")
    return validated


def ols_core(
    y: np.ndarray, predictor: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fit OLS with an intercept and return beta, residuals, and X."""
    y = np.asarray(y, dtype="float64")
    predictor = np.asarray(predictor, dtype="float64")
    design = np.column_stack([np.ones(len(y)), predictor])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if rank != design.shape[1]:
        raise ValueError("Regression design matrix is not full column rank.")

    residuals = y - design @ coefficients
    orthogonality_error = float(np.max(np.abs(design.T @ residuals)))
    scale = max(1.0, float(np.max(np.abs(design.T @ y))))
    if orthogonality_error > 1.0e-10 * scale:
        raise RuntimeError("OLS residuals failed the orthogonality check.")
    return coefficients, residuals, design


def sandwich_covariance(
    design: np.ndarray, spectral_density: np.ndarray
) -> np.ndarray:
    """Convert a score spectral density into coefficient covariance."""
    sample_size = design.shape[0]
    q_inverse = np.linalg.inv((design.T @ design) / sample_size)
    return (q_inverse @ spectral_density @ q_inverse) / sample_size


def ols_covariance(design: np.ndarray, residuals: np.ndarray) -> np.ndarray:
    """Conventional homoskedastic OLS covariance with residual degrees of freedom."""
    sample_size, n_parameters = design.shape
    residual_variance = float(residuals @ residuals) / (
        sample_size - n_parameters
    )
    return residual_variance * np.linalg.inv(design.T @ design)


def white_covariance(design: np.ndarray, residuals: np.ndarray) -> np.ndarray:
    """White (1980) HC0 covariance."""
    sample_size = design.shape[0]
    scores = design * residuals[:, None]
    spectral_density = (scores.T @ scores) / sample_size
    return sandwich_covariance(design, spectral_density)


def hac_covariance(
    design: np.ndarray,
    residuals: np.ndarray,
    max_lag: int,
    use_bartlett_weights: bool,
) -> np.ndarray:
    """HAC covariance using the problem-set footnote's lag normalization."""
    sample_size = design.shape[0]
    if not 0 <= max_lag < sample_size:
        raise ValueError(f"Invalid HAC lag {max_lag} for T={sample_size}.")

    scores = design * residuals[:, None]
    spectral_density = (scores.T @ scores) / sample_size
    for lag in range(1, max_lag + 1):
        autocovariance = (scores[lag:].T @ scores[:-lag]) / (
            sample_size - lag
        )
        weight = 1.0 - lag / (max_lag + 1.0) if use_bartlett_weights else 1.0
        spectral_density += weight * (autocovariance + autocovariance.T)
    return sandwich_covariance(design, spectral_density)


def newey_west_1994_covariance(
    design: np.ndarray, residuals: np.ndarray
) -> tuple[np.ndarray, int, int]:
    """Newey-West (1994) plug-in bandwidth with VAR(1) prewhitening."""
    sample_size, n_parameters = design.shape
    if n_parameters != 2:
        raise ValueError("This implementation expects an intercept and one predictor.")
    if sample_size < 10:
        raise ValueError("Sample is too short for automatic bandwidth selection.")

    scores = design * residuals[:, None]
    current = scores[1:]
    lagged = scores[:-1]
    a_hat = (current.T @ lagged) @ np.linalg.inv(lagged.T @ lagged)
    prewhitened = current - (a_hat @ lagged.T).T
    prewhitened_size = len(prewhitened)

    preliminary_lag = int(np.floor(4.0 * (sample_size / 100.0) ** (2.0 / 9.0)))
    preliminary_lag = min(preliminary_lag, prewhitened_size - 1)
    scalar_scores = prewhitened @ np.array([0.0, 1.0])

    autocovariances: list[float] = []
    for lag in range(preliminary_lag + 1):
        left = scalar_scores[preliminary_lag:]
        right = scalar_scores[
            preliminary_lag - lag : prewhitened_size - lag
        ]
        autocovariances.append(float(left @ right) / prewhitened_size)

    s_zero = autocovariances[0] + 2.0 * sum(autocovariances[1:])
    s_one = 2.0 * sum(
        lag * autocovariances[lag]
        for lag in range(1, preliminary_lag + 1)
    )
    if not np.isfinite(s_zero) or abs(s_zero) <= np.finfo(float).tiny:
        raise ValueError("Newey-West plug-in denominator is zero or non-finite.")

    plug_in_constant = 1.1447 * ((s_one / s_zero) ** 2) ** (1.0 / 3.0)
    bandwidth = int(np.floor(plug_in_constant * sample_size ** (1.0 / 3.0)))
    bandwidth = max(0, min(bandwidth, prewhitened_size - 1))

    spectral_prewhitened = (prewhitened.T @ prewhitened) / prewhitened_size
    for lag in range(1, bandwidth + 1):
        autocovariance = (
            prewhitened[lag:].T @ prewhitened[:-lag]
        ) / prewhitened_size
        bartlett_weight = 1.0 - lag / (bandwidth + 1.0)
        spectral_prewhitened += bartlett_weight * (
            autocovariance + autocovariance.T
        )

    recoloring = np.linalg.inv(np.eye(n_parameters) - a_hat)
    spectral_density = recoloring @ spectral_prewhitened @ recoloring.T
    covariance = sandwich_covariance(design, spectral_density)
    return covariance, bandwidth, preliminary_lag


def inference_result(
    method: str, coefficients: np.ndarray, covariance: np.ndarray
) -> InferenceResult:
    """Extract the slope standard error and t-statistic."""
    slope_variance = float(covariance[1, 1])
    if not np.isfinite(slope_variance) or slope_variance <= 0.0:
        raise ValueError(f"{method} slope variance is not positive.")
    standard_error = float(np.sqrt(slope_variance))
    slope = float(coefficients[1])
    return InferenceResult(method, slope, standard_error, slope / standard_error)


def estimate_inference_methods(
    data: pd.DataFrame,
) -> tuple[list[InferenceResult], int, int, int]:
    """Estimate Question 2b and apply all five covariance estimators."""
    validated = validate_data(data)
    regression_data = pd.DataFrame(
        {
            "dependent": validated["excess_return"].shift(-MONTHS_PER_YEAR),
            "predictor": validated["dividend_price"],
        }
    ).dropna()
    coefficients, residuals, design = ols_core(
        regression_data["dependent"].to_numpy(),
        regression_data["predictor"].to_numpy(),
    )

    automatic_covariance, automatic_bandwidth, preliminary_lag = (
        newey_west_1994_covariance(design, residuals)
    )
    covariance_methods = [
        ("OLS", ols_covariance(design, residuals)),
        ("White", white_covariance(design, residuals)),
        (
            "Newey-West (11)",
            hac_covariance(design, residuals, FIXED_BANDWIDTH, True),
        ),
        (
            "Hansen-Hodrick (11)",
            hac_covariance(design, residuals, FIXED_BANDWIDTH, False),
        ),
        ("Newey-West (data-driven)", automatic_covariance),
    ]
    results = [
        inference_result(method, coefficients, covariance)
        for method, covariance in covariance_methods
    ]
    return results, len(regression_data), automatic_bandwidth, preliminary_lag


def format_latex_table(
    results: list[InferenceResult],
    nobs: int,
    automatic_bandwidth: int,
) -> str:
    """Format the Question 2b inference comparison as a LaTeX table."""
    rows = [
        (
            f"{result.method} & {result.slope:.4f} & "
            f"{result.standard_error:.4f} & {result.t_statistic:.2f} \\\\"
        )
        for result in results
    ]
    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Predictive-regression inference for excess equity returns}",
            r"\label{tab:q2b_standard_errors}",
            r"\begin{tabular}{lrrr}",
            r"\toprule",
            r"Method & $b$ & Standard error & $t$-statistic \\",
            r"\midrule",
            *rows,
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.92\textwidth}",
            (
                r"\footnotesize\textit{Note:} All rows use the same OLS slope "
                r"estimate and $N="
                + str(nobs)
                + r"$ observations. Newey-West (11) uses Bartlett weights; "
                r"Hansen-Hodrick (11) uses uniform weights. The data-driven "
                r"Newey-West bandwidth is $L="
                + str(automatic_bandwidth)
                + r"$."
            ),
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 2b.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results, nobs, automatic_bandwidth, preliminary_lag = (
        estimate_inference_methods(pd.read_csv(args.input))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        format_latex_table(results, nobs, automatic_bandwidth),
        encoding="utf-8",
    )

    print(f"Regression observations = {nobs}")
    print(f"Automatic Newey-West preliminary lag = {preliminary_lag}")
    print(f"Automatic Newey-West bandwidth = {automatic_bandwidth}")
    for result in results:
        print(
            f"{result.method}: b = {result.slope:.8f}, "
            f"SE = {result.standard_error:.8f}, "
            f"t = {result.t_statistic:.6f}"
        )
    print(f"Saved inference table to {args.output}")


if __name__ == "__main__":
    main()
