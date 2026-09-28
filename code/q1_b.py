"""Implement the Campbell-Shiller decomposition for Question 1b.

The script reads the monthly observations of annual variables in the EQ
Dataset, constructs the discounted future-return, dividend-growth, and
terminal dividend-price components for horizons H=1,...,20 years, and writes
the requested decomposition data and figure.
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
DEFAULT_DATA_OUTPUT = Path("output/q1b_decomposition.csv")
DEFAULT_FIGURE_OUTPUT = Path("output/q1b_decomposition.pdf")
REQUIRED_COLUMNS = {"YEAR", "MONTH", "dp", "dg", "re"}
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
    for column in ("YEAR", "MONTH", "dp", "dg", "re"):
        validated[column] = pd.to_numeric(validated[column], errors="raise")

    if validated[["YEAR", "MONTH", "dp", "dg", "re"]].isna().any().any():
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

    required_length = MONTHS_PER_YEAR * MAX_HORIZON + 2
    if len(validated) < required_length:
        raise ValueError(
            f"At least {required_length} monthly observations are required "
            f"to estimate the H={MAX_HORIZON} decomposition."
        )

    return validated


def covariance_slope(x: pd.Series, y: pd.Series) -> float:
    """Return Cov(x, y) / Var(x) using one common complete-case sample."""
    x_centered = x.to_numpy(dtype="float64") - float(x.mean())
    y_centered = y.to_numpy(dtype="float64") - float(y.mean())
    denominator = float(x_centered @ x_centered)
    if denominator <= 0.0:
        raise ValueError("The complete-case dp sample has zero variance.")
    return float((x_centered @ y_centered) / denominator)


def calculate_decomposition(data: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """Calculate b_re(H), b_dg(H), b_dp(H), and their sum for H=1,...,20."""
    validated = validate_data(data)
    kappa = float(1.0 / (1.0 + np.exp(validated["dp"].mean())))

    discounted_returns = pd.Series(0.0, index=validated.index, dtype="float64")
    discounted_growth = pd.Series(0.0, index=validated.index, dtype="float64")
    rows: list[dict[str, float | int]] = []

    for horizon in range(MIN_HORIZON, MAX_HORIZON + 1):
        lead = MONTHS_PER_YEAR * horizon
        weight = kappa ** (horizon - 1)
        discounted_returns = discounted_returns + weight * validated["re"].shift(-lead)
        discounted_growth = discounted_growth + weight * validated["dg"].shift(-lead)
        terminal_dp = (kappa**horizon) * validated["dp"].shift(-lead)

        sample = pd.DataFrame(
            {
                "dp": validated["dp"],
                "return_component": discounted_returns,
                "growth_component": -discounted_growth,
                "terminal_component": terminal_dp,
            }
        ).dropna()

        expected_observations = len(validated) - lead
        if len(sample) != expected_observations:
            raise ValueError(
                f"H={horizon} has {len(sample)} complete observations; "
                f"expected {expected_observations}."
            )

        b_re = covariance_slope(sample["dp"], sample["return_component"])
        b_dg = covariance_slope(sample["dp"], sample["growth_component"])
        b_dp = covariance_slope(sample["dp"], sample["terminal_component"])
        rows.append(
            {
                "H": horizon,
                "b_re": b_re,
                "b_dg": b_dg,
                "b_dp": b_dp,
                "sum_b": b_re + b_dg + b_dp,
                "nobs": len(sample),
            }
        )

    return kappa, pd.DataFrame(rows)


def create_figure(results: pd.DataFrame, output_path: Path) -> None:
    """Plot the three Question 1b decomposition coefficients."""
    figure, axis = plt.subplots(figsize=(8.0, 5.25))
    axis.plot(
        results["H"],
        results["b_re"],
        color="#1f77b4",
        marker="o",
        markersize=4,
        linewidth=1.8,
        label=r"$b_{re}^{(H)}$",
    )
    axis.plot(
        results["H"],
        results["b_dg"],
        color="#d62728",
        marker="s",
        markersize=4,
        linewidth=1.8,
        label=r"$b_{\Delta d}^{(H)}$",
    )
    axis.plot(
        results["H"],
        results["b_dp"],
        color="#2ca02c",
        marker="^",
        markersize=4,
        linewidth=1.8,
        label=r"$b_{dp}^{(H)}$",
    )
    axis.axhline(0.0, color="black", linewidth=0.8, alpha=0.7)
    axis.set_xlabel("Horizon, $H$ (years)")
    axis.set_ylabel("Decomposition coefficient")
    axis.set_xticks(range(MIN_HORIZON, MAX_HORIZON + 1))
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7, alpha=0.8)
    axis.legend(frameon=False)
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Construct the Question 1b Campbell-Shiller decomposition."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT)
    parser.add_argument("--figure-output", type=Path, default=DEFAULT_FIGURE_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = pd.read_csv(args.input)
    kappa, results = calculate_decomposition(data)

    args.data_output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.data_output, index=False, float_format="%.12f")
    create_figure(results, args.figure_output)

    max_identity_gap = float((results["sum_b"] - 1.0).abs().max())
    print(f"kappa = {kappa:.12f}")
    print(f"Maximum |sum_b(H) - 1| = {max_identity_gap:.12f}")
    print(f"Saved decomposition values to {args.data_output}")
    print(f"Saved decomposition figure to {args.figure_output}")


if __name__ == "__main__":
    main()
