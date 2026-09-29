"""Implement the Amihud-Hurvich bias correction for Question 2c."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_OUTPUT = Path("output/q2c_bias_correction.csv")
REQUIRED_COLUMNS = {"YEAR", "MONTH", "dp", "rf", "re"}
MONTHS_PER_YEAR = 12


def validate_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate, date, chronologically sort, and transform the equity data."""
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


def fit_ols(y: np.ndarray, regressors: np.ndarray) -> np.ndarray:
    """Estimate OLS with an intercept and return the coefficient vector."""
    y = np.asarray(y, dtype="float64")
    regressors = np.asarray(regressors, dtype="float64")
    if regressors.ndim == 1:
        regressors = regressors[:, None]
    design = np.column_stack([np.ones(len(y)), regressors])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if rank != design.shape[1]:
        raise ValueError("Regression design matrix is not full column rank.")
    return coefficients


def estimate_bias_correction(data: pd.DataFrame) -> pd.DataFrame:
    """Estimate the three steps specified for Question 2c."""
    validated = validate_data(data)
    first_period = pd.Period(validated["date"].iloc[0], freq="M")
    last_period = pd.Period(validated["date"].iloc[-1], freq="M")
    total_years = (last_period.ordinal - first_period.ordinal) / MONTHS_PER_YEAR
    if total_years <= 0.0:
        raise ValueError("The dataset must span a positive number of years.")

    regression_data = pd.DataFrame(
        {
            "dividend_price": validated["dividend_price"],
            "future_dividend_price": validated["dividend_price"].shift(
                -MONTHS_PER_YEAR
            ),
            "future_excess_return": validated["excess_return"].shift(
                -MONTHS_PER_YEAR
            ),
        }
    ).dropna()
    expected_nobs = len(validated) - MONTHS_PER_YEAR
    if len(regression_data) != expected_nobs:
        raise RuntimeError(
            f"Question 2c should use {expected_nobs} observations; "
            f"found {len(regression_data)}."
        )

    theta_hat, phi_hat = fit_ols(
        regression_data["future_dividend_price"].to_numpy(),
        regression_data["dividend_price"].to_numpy(),
    )
    phi_c = (
        phi_hat
        + (1.0 / total_years) * (1.0 + 3.0 * phi_hat)
        + (3.0 / total_years**2) * (1.0 + 3.0 * phi_hat)
    )
    regression_data["u_c"] = regression_data["future_dividend_price"] - (
        theta_hat + phi_c * regression_data["dividend_price"]
    )

    a_q2c, b_q2c, b_u = fit_ols(
        regression_data["future_excess_return"].to_numpy(),
        regression_data[["dividend_price", "u_c"]].to_numpy(),
    )
    _, b_q2b = fit_ols(
        regression_data["future_excess_return"].to_numpy(),
        regression_data["dividend_price"].to_numpy(),
    )

    return pd.DataFrame(
        [
            {
                "theta_hat": theta_hat,
                "phi_hat": phi_hat,
                "T_years": total_years,
                "phi_c": phi_c,
                "a_q2c": a_q2c,
                "b_q2c": b_q2c,
                "b_q2b": b_q2b,
                "b_u": b_u,
                "b_q2c_minus_b_q2b": b_q2c - b_q2b,
                "nobs": len(regression_data),
            }
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 2c.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = estimate_bias_correction(pd.read_csv(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output, index=False, float_format="%.12f")

    row = results.iloc[0]
    print(f"T (years) = {row['T_years']:.6f}")
    print(f"theta_hat = {row['theta_hat']:.12f}")
    print(f"phi_hat = {row['phi_hat']:.12f}")
    print(f"phi_c = {row['phi_c']:.12f}")
    print(f"b from Q2c = {row['b_q2c']:.12f}")
    print(f"b from Q2b = {row['b_q2b']:.12f}")
    print(f"b_u = {row['b_u']:.12f}")
    print(f"b_Q2c - b_Q2b = {row['b_q2c_minus_b_q2b']:.12f}")
    print(f"Observations = {int(row['nobs'])}")
    print(f"Saved bias-correction results to {args.output}")


if __name__ == "__main__":
    main()
