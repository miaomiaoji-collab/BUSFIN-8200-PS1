"""Implement the VAR-implied Campbell-Shiller decomposition for Question 1c.

The script estimates the annual-transition VAR specified in ``spec/q1.md``
using monthly observations, recursively constructs conditional expectations
for horizons H=1,...,20 years, and writes the requested decomposition data and
figure.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_DATA_OUTPUT = Path("output/q1c_decomposition.csv")
DEFAULT_FIGURE_OUTPUT = Path("output/q1c_decomposition.pdf")
STATE_COLUMNS = ["dg", "re", "dp"]
REQUIRED_COLUMNS = {"YEAR", "MONTH", *STATE_COLUMNS}
MIN_HORIZON = 1
MAX_HORIZON = 20
MONTHS_PER_YEAR = 12


def validate_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate, date, and chronologically sort the monthly EQ dataset."""
    missing_columns = REQUIRED_COLUMNS.difference(data.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"EQ Dataset is missing required columns: {missing}")

    validated = data.copy()
    for column in ("YEAR", "MONTH", *STATE_COLUMNS):
        validated[column] = pd.to_numeric(validated[column], errors="raise")

    if validated[["YEAR", "MONTH", *STATE_COLUMNS]].isna().any().any():
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
    duplicate_dates = validated["date"].duplicated(keep=False)
    if duplicate_dates.any():
        examples = validated.loc[duplicate_dates, ["YEAR", "MONTH"]].head()
        raise ValueError(
            "Duplicate monthly observations found: "
            f"{examples.to_dict(orient='records')}"
        )

    validated = validated.sort_values("date", kind="stable").reset_index(drop=True)
    observed = pd.PeriodIndex(validated["date"], freq="M")
    expected = pd.period_range(observed.min(), observed.max(), freq="M")
    if not observed.equals(expected):
        missing = expected.difference(observed)
        examples = [str(period) for period in missing[:5]]
        raise ValueError(
            "The dataset must be a continuous monthly series. "
            f"Missing examples: {examples}"
        )

    if len(validated) <= MONTHS_PER_YEAR:
        raise ValueError("The dataset is too short to estimate a 12-month-ahead VAR.")

    return validated


def estimate_var(
    data: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Estimate z_(t+12) = intercept + Gamma z_t + error by OLS."""
    states = data[STATE_COLUMNS].to_numpy(dtype="float64")
    current_states = states[:-MONTHS_PER_YEAR]
    future_states = states[MONTHS_PER_YEAR:]
    design = np.column_stack([np.ones(len(current_states)), current_states])

    coefficients, _, rank, _ = np.linalg.lstsq(design, future_states, rcond=None)
    if rank != design.shape[1]:
        raise ValueError("The VAR design matrix is not full column rank.")

    intercept = coefficients[0]
    gamma = coefficients[1:].T
    residuals = future_states - design @ coefficients
    orthogonality_error = float(np.max(np.abs(design.T @ residuals)))
    scale = max(1.0, float(np.max(np.abs(design.T @ future_states))))
    if orthogonality_error > 1e-10 * scale:
        raise RuntimeError("VAR residuals failed the OLS orthogonality check.")

    return intercept, gamma, current_states


def covariance_slope(x: np.ndarray, y: np.ndarray) -> float:
    """Return Cov(x, y) / Var(x) on the fixed VAR estimation sample."""
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    denominator = float(x_centered @ x_centered)
    if denominator <= 0.0:
        raise ValueError("The VAR estimation sample has zero dp variance.")
    return float((x_centered @ y_centered) / denominator)


def calculate_var_decomposition(
    data: pd.DataFrame,
) -> tuple[float, np.ndarray, np.ndarray, pd.DataFrame]:
    """Estimate the VAR and calculate its decomposition for H=1,...,20."""
    validated = validate_data(data)
    kappa = float(1.0 / (1.0 + np.exp(validated["dp"].mean())))
    intercept, gamma, current_states = estimate_var(validated)

    dg_index = STATE_COLUMNS.index("dg")
    re_index = STATE_COLUMNS.index("re")
    dp_index = STATE_COLUMNS.index("dp")
    current_dp = current_states[:, dp_index]
    forecasts = current_states.copy()
    discounted_returns = np.zeros(len(current_states), dtype="float64")
    discounted_growth = np.zeros(len(current_states), dtype="float64")
    rows: list[dict[str, float | int]] = []

    for horizon in range(MIN_HORIZON, MAX_HORIZON + 1):
        forecasts = intercept + forecasts @ gamma.T
        weight = kappa ** (horizon - 1)
        discounted_returns += weight * forecasts[:, re_index]
        discounted_growth += weight * forecasts[:, dg_index]
        terminal_component = (kappa**horizon) * forecasts[:, dp_index]

        b_re = covariance_slope(current_dp, discounted_returns)
        b_dg = covariance_slope(current_dp, -discounted_growth)
        b_dp = covariance_slope(current_dp, terminal_component)
        rows.append(
            {
                "H": horizon,
                "b_re_VAR": b_re,
                "b_dg_VAR": b_dg,
                "b_dp_VAR": b_dp,
                "sum_b_VAR": b_re + b_dg + b_dp,
                "nobs": len(current_states),
            }
        )

    return kappa, intercept, gamma, pd.DataFrame(rows)


def create_figure(results: pd.DataFrame, output_path: Path) -> None:
    """Plot the three VAR-implied Question 1c decomposition coefficients."""
    figure, axis = plt.subplots(figsize=(8.0, 5.25))
    axis.plot(
        results["H"],
        results["b_re_VAR"],
        color="#ff0000",
        marker="o",
        markersize=4,
        linewidth=1.8,
        label=r"$b_{re,\mathrm{VAR}}^{(H)}$",
    )
    axis.plot(
        results["H"],
        results["b_dg_VAR"],
        color="#0000ff",
        marker="s",
        markersize=4,
        linewidth=1.8,
        label=r"$b_{\Delta d,\mathrm{VAR}}^{(H)}$",
    )
    axis.plot(
        results["H"],
        results["b_dp_VAR"],
        color="#00ff00",
        marker="^",
        markersize=4,
        linewidth=1.8,
        label=r"$b_{dp,\mathrm{VAR}}^{(H)}$",
    )
    axis.axhline(0.0, color="black", linewidth=0.8, alpha=0.7)
    axis.set_xlabel("Horizon, $H$ (years)")
    axis.set_ylabel("VAR-implied decomposition coefficient")
    axis.set_xticks(range(MIN_HORIZON, MAX_HORIZON + 1))
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7, alpha=0.8)
    axis.legend(frameon=False)
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Construct the Question 1c VAR-implied decomposition."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT)
    parser.add_argument("--figure-output", type=Path, default=DEFAULT_FIGURE_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = pd.read_csv(args.input)
    kappa, intercept, gamma, results = calculate_var_decomposition(data)

    args.data_output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.data_output, index=False, float_format="%.12f")
    create_figure(results, args.figure_output)

    spectral_radius = float(np.max(np.abs(np.linalg.eigvals(gamma))))
    max_identity_gap = float((results["sum_b_VAR"] - 1.0).abs().max())
    print(f"kappa = {kappa:.12f}")
    print(f"VAR estimation observations = {len(data) - MONTHS_PER_YEAR}")
    print(f"VAR spectral radius = {spectral_radius:.12f}")
    print(f"Maximum |sum_b_VAR(H) - 1| = {max_identity_gap:.12f}")
    print("VAR intercept:", np.array2string(intercept, precision=10))
    print("VAR Gamma:\n", np.array2string(gamma, precision=10))
    print(f"Saved decomposition values to {args.data_output}")
    print(f"Saved decomposition figure to {args.figure_output}")


if __name__ == "__main__":
    main()
