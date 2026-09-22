"""Estimate the predictive regressions for Questions 4b and 4c.

The script reads the monthly bond variables created by ``code/q4_a.py``.
Question 4b uses Hansen-Hodrick standard errors with uniform weights and
L = 12H - 1. Question 4c uses Bartlett Newey-West standard errors with the
automatic bandwidth procedure in equation (2.2) of Newey and West (1994),
including the paper's recommended VAR(1) prewhitening.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("output/q4_bond_variables.csv")
DEFAULT_Q4B_OUTPUT = Path("output/q4b_table.tex")
DEFAULT_Q4C_OUTPUT = Path("output/q4c_table.tex")
MATURITIES = [1, 2, 3, 4, 5]
REGRESSION_MATURITIES = [2, 3, 4, 5]
MONTHS_PER_YEAR = 12
REQUIRED_COLUMNS = {"month", "H", "xy", "xf", "xr"}


@dataclass(frozen=True)
class RegressionResult:
    """OLS estimates and the requested robust inference statistics."""

    intercept: float
    slope: float
    slope_t_stat: float
    r_squared: float
    nobs: int
    bandwidth: int
    preliminary_lag: int | None = None


def load_bond_variables(path: Path) -> pd.DataFrame:
    """Read and validate the monthly long-form Question 4a output."""
    data = pd.read_csv(path)
    missing_columns = REQUIRED_COLUMNS.difference(data.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Input data are missing required columns: {missing}")

    data = data.copy()
    data["month"] = pd.to_datetime(
        data["month"], format="%Y-%m", errors="raise"
    ).dt.strftime("%Y-%m")
    data["H"] = pd.to_numeric(data["H"], errors="raise")
    if data[["month", "H"]].isna().any().any():
        raise ValueError("Input data contain missing month or H values.")
    if not np.equal(data["H"], np.floor(data["H"])).all():
        raise ValueError("Maturity H must be integer-valued.")
    data["H"] = data["H"].astype("int64")

    observed_maturities = sorted(data["H"].unique().tolist())
    if observed_maturities != MATURITIES:
        raise ValueError(
            f"Expected maturities {MATURITIES}, found {observed_maturities}."
        )
    if data.duplicated(["month", "H"]).any():
        raise ValueError("Input data contain duplicate month-maturity observations.")
    if not data.groupby("month")["H"].nunique().eq(len(MATURITIES)).all():
        raise ValueError("Every month must contain H=1,...,5.")

    observed_months = pd.PeriodIndex(sorted(data["month"].unique()), freq="M")
    expected_months = pd.period_range(
        observed_months.min(), observed_months.max(), freq="M"
    )
    missing_months = expected_months.difference(observed_months)
    if not missing_months.empty:
        examples = [str(month) for month in missing_months[:5]]
        raise ValueError(f"Monthly panel has calendar gaps: {examples}")

    for column in ("xy", "xf", "xr"):
        data[column] = pd.to_numeric(data[column], errors="coerce")

    return data.sort_values(["month", "H"], kind="stable").reset_index(drop=True)


def to_wide(data: pd.DataFrame) -> pd.DataFrame:
    """Reshape xy, xf, and xr to monthly matrices indexed by maturity."""
    wide = data.pivot(index="month", columns="H", values=["xy", "xf", "xr"])
    wide = wide.sort_index()
    # Question 4b explicitly defines the one-year excess return as zero.
    wide[("xr", 1)] = 0.0
    return wide.sort_index(axis="columns")


def ols_core(y: np.ndarray, predictor: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Fit OLS with an intercept and return beta, residuals, X, and R-squared."""
    y = np.asarray(y, dtype=float)
    predictor = np.asarray(predictor, dtype=float)
    x = np.column_stack([np.ones(y.size), predictor])
    beta, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank != x.shape[1]:
        raise ValueError("Regression design matrix is rank deficient.")

    residuals = y - x @ beta
    centered = y - y.mean()
    total_sum_squares = float(centered @ centered)
    if total_sum_squares == 0:
        raise ValueError("Dependent variable has no sample variation.")
    r_squared = 1.0 - float(residuals @ residuals) / total_sum_squares
    return beta, residuals, x, r_squared


def sandwich_covariance(x: np.ndarray, spectral_density: np.ndarray) -> np.ndarray:
    """Convert a score spectral-density estimate into coefficient covariance."""
    sample_size = x.shape[0]
    q_inverse = np.linalg.inv((x.T @ x) / sample_size)
    return (q_inverse @ spectral_density @ q_inverse) / sample_size


def hansen_hodrick_covariance(
    x: np.ndarray, residuals: np.ndarray, max_lag: int
) -> np.ndarray:
    """Hansen-Hodrick HAC covariance with uniform weights through max_lag."""
    sample_size = x.shape[0]
    if not 0 <= max_lag < sample_size:
        raise ValueError(f"Invalid Hansen-Hodrick lag {max_lag} for T={sample_size}.")

    scores = x * residuals[:, None]
    spectral_density = (scores.T @ scores) / sample_size
    for lag in range(1, max_lag + 1):
        autocovariance = (scores[lag:].T @ scores[:-lag]) / (sample_size - lag)
        spectral_density += autocovariance + autocovariance.T
    return sandwich_covariance(x, spectral_density)


def newey_west_1994_covariance(
    x: np.ndarray, residuals: np.ndarray
) -> tuple[np.ndarray, int, int]:
    """Newey-West (1994) equation (2.2) Bartlett covariance and bandwidth.

    The implementation follows the paper's recommended procedure: form the OLS
    score vector h_t = x_t * residual_t, fit a zero-intercept VAR(1), estimate the
    plug-in constant using w=(0,1)' (the first regressor is the intercept), apply
    the Bartlett kernel to the prewhitened scores, and recolor the estimate.
    """
    sample_size, n_parameters = x.shape
    if n_parameters != 2:
        raise ValueError("This implementation expects an intercept and one predictor.")
    if sample_size < 10:
        raise ValueError("Sample is too short for Newey-West automatic bandwidth selection.")

    scores = x * residuals[:, None]
    current = scores[1:]
    lagged = scores[:-1]
    lag_cross_product = lagged.T @ lagged
    a_hat = (current.T @ lagged) @ np.linalg.inv(lag_cross_product)
    prewhitened = current - (a_hat @ lagged.T).T
    prewhitened_size = prewhitened.shape[0]

    preliminary_lag = int(np.floor(4.0 * (sample_size / 100.0) ** (2.0 / 9.0)))
    preliminary_lag = min(preliminary_lag, prewhitened_size - 1)
    weight_vector = np.array([0.0, 1.0])
    scalar_scores = prewhitened @ weight_vector

    sigma = []
    for lag in range(preliminary_lag + 1):
        left = scalar_scores[preliminary_lag:]
        right = scalar_scores[
            preliminary_lag - lag : prewhitened_size - lag
        ]
        sigma.append(float(left @ right) / prewhitened_size)

    s_zero = sigma[0] + 2.0 * sum(sigma[1:])
    s_one = 2.0 * sum(lag * sigma[lag] for lag in range(1, preliminary_lag + 1))
    if not np.isfinite(s_zero) or abs(s_zero) <= np.finfo(float).tiny:
        raise ValueError("Newey-West (1994) plug-in denominator is zero or non-finite.")

    gamma_hat = 1.1447 * ((s_one / s_zero) ** 2) ** (1.0 / 3.0)
    bandwidth = int(np.floor(gamma_hat * sample_size ** (1.0 / 3.0)))
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
    covariance = sandwich_covariance(x, spectral_density)
    return covariance, bandwidth, preliminary_lag


def result_from_covariance(
    beta: np.ndarray,
    covariance: np.ndarray,
    r_squared: float,
    nobs: int,
    bandwidth: int,
    preliminary_lag: int | None = None,
) -> RegressionResult:
    """Collect regression output and validate the slope variance."""
    slope_variance = float(covariance[1, 1])
    if not np.isfinite(slope_variance) or slope_variance <= 0:
        raise ValueError(f"Slope variance is not positive: {slope_variance}")
    slope_t_stat = float(beta[1] / np.sqrt(slope_variance))
    return RegressionResult(
        intercept=float(beta[0]),
        slope=float(beta[1]),
        slope_t_stat=slope_t_stat,
        r_squared=float(r_squared),
        nobs=int(nobs),
        bandwidth=int(bandwidth),
        preliminary_lag=preliminary_lag,
    )


def estimate_q4b(wide: pd.DataFrame) -> dict[int, RegressionResult]:
    """Estimate the hold-to-maturity regressions for Question 4b."""
    results: dict[int, RegressionResult] = {}
    for maturity in REGRESSION_MATURITIES:
        hold_to_maturity = pd.Series(0.0, index=wide.index)
        for year_ahead in range(1, maturity + 1):
            remaining_maturity = maturity - year_ahead + 1
            if remaining_maturity == 1:
                # xr(1) is defined as zero, including beyond the observed sample.
                continue
            hold_to_maturity += wide[("xr", remaining_maturity)].shift(
                -MONTHS_PER_YEAR * year_ahead
            )

        regression_data = pd.DataFrame(
            {
                "dependent": hold_to_maturity / maturity,
                "predictor": wide[("xy", maturity)],
            }
        ).dropna()
        beta, residuals, x, r_squared = ols_core(
            regression_data["dependent"].to_numpy(),
            regression_data["predictor"].to_numpy(),
        )
        bandwidth = MONTHS_PER_YEAR * maturity - 1
        covariance = hansen_hodrick_covariance(x, residuals, bandwidth)
        results[maturity] = result_from_covariance(
            beta, covariance, r_squared, len(regression_data), bandwidth
        )
    return results


def estimate_q4c(wide: pd.DataFrame) -> dict[int, RegressionResult]:
    """Estimate the one-year-ahead forward-rate regressions for Question 4c."""
    results: dict[int, RegressionResult] = {}
    for maturity in REGRESSION_MATURITIES:
        regression_data = pd.DataFrame(
            {
                "dependent": wide[("xr", maturity)].shift(-MONTHS_PER_YEAR),
                "predictor": wide[("xf", maturity)],
            }
        ).dropna()
        beta, residuals, x, r_squared = ols_core(
            regression_data["dependent"].to_numpy(),
            regression_data["predictor"].to_numpy(),
        )
        covariance, bandwidth, preliminary_lag = newey_west_1994_covariance(
            x, residuals
        )
        results[maturity] = result_from_covariance(
            beta,
            covariance,
            r_squared,
            len(regression_data),
            bandwidth,
            preliminary_lag,
        )
    return results


def format_regression_table(
    results: dict[int, RegressionResult], caption: str, label: str, note: str
) -> str:
    """Format slopes, robust t-statistics, R-squared, and sample sizes in LaTeX."""
    columns = " & ".join(f"$H={maturity}$" for maturity in REGRESSION_MATURITIES)
    slopes = " & ".join(
        f"{results[maturity].slope:.3f} [{results[maturity].slope_t_stat:.2f}]"
        for maturity in REGRESSION_MATURITIES
    )
    r_squared = " & ".join(
        f"{results[maturity].r_squared:.3f}"
        for maturity in REGRESSION_MATURITIES
    )
    observations = " & ".join(
        str(results[maturity].nobs) for maturity in REGRESSION_MATURITIES
    )

    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            f"\\caption{{{caption}}}",
            f"\\label{{{label}}}",
            r"\begin{tabular}{lrrrr}",
            r"\toprule",
            f" & {columns} \\\\",
            r"\midrule",
            f"$b^{{(H)}}$ [t-statistic] & {slopes} \\\\",
            f"$R^2$ & {r_squared} \\\\",
            f"Observations & {observations} \\\\",
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.92\textwidth}",
            r"\footnotesize\textit{Note:} " + note,
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate the Question 4b and 4c bond-return regressions."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--q4b-output", type=Path, default=DEFAULT_Q4B_OUTPUT)
    parser.add_argument("--q4c-output", type=Path, default=DEFAULT_Q4C_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_bond_variables(args.input)
    wide = to_wide(data)
    q4b_results = estimate_q4b(wide)
    q4c_results = estimate_q4c(wide)

    q4b_note = (
        "OLS estimates. Brackets contain Hansen--Hodrick (1980) t-statistics "
        "using uniform weights and $L=12H-1$ monthly lags."
    )
    q4c_note = (
        "OLS estimates. Brackets contain Bartlett Newey--West (1987) "
        "t-statistics using the automatic bandwidth and VAR(1) prewhitening "
        "procedure in Newey and West (1994), equation (2.2)."
    )
    q4b_table = format_regression_table(
        q4b_results,
        "Hold-to-maturity excess-return predictability",
        "tab:q4b",
        q4b_note,
    )
    q4c_table = format_regression_table(
        q4c_results,
        "One-year excess-return predictability",
        "tab:q4c",
        q4c_note,
    )

    args.q4b_output.parent.mkdir(parents=True, exist_ok=True)
    args.q4c_output.parent.mkdir(parents=True, exist_ok=True)
    args.q4b_output.write_text(q4b_table, encoding="utf-8")
    args.q4c_output.write_text(q4c_table, encoding="utf-8")

    print("Question 4b results:")
    for maturity, result in q4b_results.items():
        print(
            f"  H={maturity}: b={result.slope:.6f}, "
            f"t={result.slope_t_stat:.3f}, R2={result.r_squared:.6f}, "
            f"N={result.nobs}, HH L={result.bandwidth}"
        )
    print("Question 4c results:")
    for maturity, result in q4c_results.items():
        print(
            f"  H={maturity}: b={result.slope:.6f}, "
            f"t={result.slope_t_stat:.3f}, R2={result.r_squared:.6f}, "
            f"N={result.nobs}, NW94 n={result.preliminary_lag}, "
            f"L={result.bandwidth}"
        )
    print(f"Saved {args.q4b_output} and {args.q4c_output}.")


if __name__ == "__main__":
    main()
