"""Implement the predictive-return regressions for Question 2a."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_FIGURE_OUTPUT = Path("output/q2a_adj_r2.pdf")
DEFAULT_TABLE_OUTPUT = Path("output/q2a_results.tex")
REQUIRED_COLUMNS = {"YEAR", "MONTH", "dp", "rf", "re"}
MONTHS_PER_YEAR = 12
MIN_HORIZON = 1
MAX_HORIZON = 15


def validate_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and chronologically sort the monthly equity dataset."""
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


def fit_ols(y: np.ndarray, predictor: np.ndarray) -> tuple[float, float, float]:
    """Return the intercept, slope, and adjusted R-squared from OLS."""
    y = np.asarray(y, dtype="float64")
    predictor = np.asarray(predictor, dtype="float64")
    design = np.column_stack([np.ones(len(y)), predictor])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if rank != design.shape[1]:
        raise ValueError("Regression design matrix is not full column rank.")

    residuals = y - design @ coefficients
    centered = y - y.mean()
    total_sum_squares = float(centered @ centered)
    if total_sum_squares <= 0.0:
        raise ValueError("Dependent variable has no sample variation.")
    r_squared = 1.0 - float(residuals @ residuals) / total_sum_squares
    nobs = len(y)
    n_parameters = design.shape[1]
    adjusted_r_squared = 1.0 - (1.0 - r_squared) * (nobs - 1) / (
        nobs - n_parameters
    )
    return float(coefficients[0]), float(coefficients[1]), adjusted_r_squared


def estimate_horizon_regressions(data: pd.DataFrame) -> pd.DataFrame:
    """Estimate the Question 2a regression separately for H=1,...,15."""
    validated = validate_data(data)
    future_excess_returns = [
        validated["excess_return"].shift(-MONTHS_PER_YEAR * horizon)
        for horizon in range(1, MAX_HORIZON + 1)
    ]
    cumulative_future_returns = pd.Series(0.0, index=validated.index)
    rows: list[dict[str, float | int]] = []

    for horizon in range(MIN_HORIZON, MAX_HORIZON + 1):
        cumulative_future_returns = (
            cumulative_future_returns + future_excess_returns[horizon - 1]
        )
        regression_data = pd.DataFrame(
            {
                "dependent": cumulative_future_returns / horizon,
                "predictor": validated["dividend_price"],
            }
        ).dropna()
        intercept, slope, adjusted_r_squared = fit_ols(
            regression_data["dependent"].to_numpy(),
            regression_data["predictor"].to_numpy(),
        )
        expected_nobs = len(validated) - MONTHS_PER_YEAR * horizon
        if len(regression_data) != expected_nobs:
            raise RuntimeError(
                f"H={horizon} should use {expected_nobs} observations; "
                f"found {len(regression_data)}."
            )
        rows.append(
            {
                "H": horizon,
                "intercept": intercept,
                "slope": slope,
                "adjusted_r_squared": adjusted_r_squared,
                "nobs": len(regression_data),
            }
        )

    return pd.DataFrame(rows)


def create_figure(results: pd.DataFrame, output_path: Path) -> None:
    """Plot adjusted R-squared against the forecasting horizon."""
    figure, axis = plt.subplots(figsize=(7.5, 4.8))
    axis.plot(
        results["H"],
        results["adjusted_r_squared"],
        color="#1f4e79",
        marker="o",
        markersize=4.5,
        linewidth=1.8,
    )
    axis.set_xlabel("Horizon, $H$ (years)")
    axis.set_ylabel("Adjusted $R^2$")
    axis.set_xticks(range(MIN_HORIZON, MAX_HORIZON + 1))
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7)
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def format_latex_table(results: pd.DataFrame) -> str:
    """Format the Question 2a estimates as a booktabs LaTeX table."""
    rows = [
        (
            f"{int(row.H)} & {row.intercept:.4f} & {row.slope:.4f} & "
            f"{row.adjusted_r_squared:.4f} & {int(row.nobs)} \\\\"
        )
        for row in results.itertuples(index=False)
    ]
    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Predictive regressions of average future excess equity returns}",
            r"\label{tab:q2a_results}",
            r"\begin{tabular}{rrrrr}",
            r"\toprule",
            r"$H$ & $a^{(H)}$ & $b^{(H)}$ & Adjusted $R^2$ & $N$ \\",
            r"\midrule",
            *rows,
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.92\textwidth}",
            r"\footnotesize\textit{Note:} For each horizon $H$, the dependent variable is the average of annual simple excess returns at years $t+1$ through $t+H$. The predictor is $D_t/P_t$.",
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 2a.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--figure-output", type=Path, default=DEFAULT_FIGURE_OUTPUT)
    parser.add_argument("--table-output", type=Path, default=DEFAULT_TABLE_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = estimate_horizon_regressions(pd.read_csv(args.input))
    create_figure(results, args.figure_output)
    args.table_output.parent.mkdir(parents=True, exist_ok=True)
    args.table_output.write_text(format_latex_table(results), encoding="utf-8")

    print(results.to_string(index=False, float_format=lambda value: f"{value:.6f}"))
    print(f"Saved adjusted-R-squared figure to {args.figure_output}")
    print(f"Saved regression table to {args.table_output}")


if __name__ == "__main__":
    main()
