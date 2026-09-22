"""Estimate the Cochrane-Piazzesi factor and Question 4e regressions.

The script reads the monthly bond variables created for Question 4a, estimates
the Question 4d Cochrane-Piazzesi factor, merges that factor into the long-form
bond dataset, produces the recession-shaded factor plot, and estimates the
Question 4e predictive regressions using the same Newey-West (1987, 1994)
procedure implemented in ``code/q4_bc.py``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from q4_bc import (
    MONTHS_PER_YEAR,
    REGRESSION_MATURITIES,
    RegressionResult,
    format_regression_table,
    load_bond_variables,
    newey_west_1994_covariance,
    ols_core,
    result_from_covariance,
)


DEFAULT_INPUT = Path("output/q4_bond_variables.csv")
DEFAULT_RECESSION_INPUT = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=USREC"
DEFAULT_DATA_OUTPUT = Path("output/q4_bond_variables_with_cp.csv")
DEFAULT_PLOT_OUTPUT = Path("output/q4d_cp_plot.pdf")
DEFAULT_Q4D_TABLE_OUTPUT = Path("output/q4d_table.tex")
DEFAULT_Q4E_TABLE_OUTPUT = Path("output/q4e_table.tex")
FORWARD_MATURITIES = [1, 2, 3, 4, 5]


@dataclass(frozen=True)
class CPResult:
    """Question 4d coefficient estimates and regression diagnostics."""

    coefficients: np.ndarray
    r_squared: float
    nobs: int


def prepare_wide_data(data: pd.DataFrame) -> pd.DataFrame:
    """Reshape forward rates and excess returns to one row per month."""
    if "f" not in data.columns:
        raise ValueError("Input data are missing the required f column.")
    data = data.copy()
    data["f"] = pd.to_numeric(data["f"], errors="raise")
    wide = data.pivot(index="month", columns="H", values=["f", "xr"])
    wide = wide.sort_index()

    if wide["f"].reindex(columns=FORWARD_MATURITIES).isna().any().any():
        raise ValueError("Forward rates f(1),...,f(5) must be available every month.")
    return wide


def estimate_cp_factor(wide: pd.DataFrame) -> tuple[CPResult, pd.Series]:
    """Estimate Question 4d and calculate cp_t for every sample month."""
    future_excess_returns = wide["xr"].reindex(
        columns=REGRESSION_MATURITIES
    ).shift(-MONTHS_PER_YEAR)
    average_future_excess_return = future_excess_returns.sum(
        axis="columns", min_count=len(REGRESSION_MATURITIES)
    ) / len(REGRESSION_MATURITIES)

    forward_rates = wide["f"].reindex(columns=FORWARD_MATURITIES)
    regression_data = pd.concat(
        [average_future_excess_return.rename("avg_xr"), forward_rates], axis="columns"
    ).dropna()
    y = regression_data["avg_xr"].to_numpy(dtype=float)
    x = np.column_stack(
        [
            np.ones(len(regression_data)),
            regression_data[FORWARD_MATURITIES].to_numpy(dtype=float),
        ]
    )
    coefficients, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank != x.shape[1]:
        raise ValueError("Question 4d regression design matrix is rank deficient.")

    residuals = y - x @ coefficients
    centered = y - y.mean()
    total_sum_squares = float(centered @ centered)
    if total_sum_squares == 0:
        raise ValueError("Question 4d dependent variable has no sample variation.")
    r_squared = 1.0 - float(residuals @ residuals) / total_sum_squares

    full_x = np.column_stack(
        [np.ones(len(forward_rates)), forward_rates.to_numpy(dtype=float)]
    )
    cp = pd.Series(full_x @ coefficients, index=forward_rates.index, name="cp")
    result = CPResult(
        coefficients=coefficients,
        r_squared=float(r_squared),
        nobs=len(regression_data),
    )
    return result, cp


def merge_cp(data: pd.DataFrame, cp: pd.Series) -> pd.DataFrame:
    """Attach the monthly CP factor to every maturity observation."""
    cp_frame = cp.rename_axis("month").reset_index()
    merged = data.merge(cp_frame, on="month", how="left", validate="many_to_one")
    if merged["cp"].isna().any():
        raise ValueError("CP factor failed to merge onto every bond observation.")
    return merged


def estimate_q4e(
    wide: pd.DataFrame, cp: pd.Series
) -> dict[int, RegressionResult]:
    """Estimate the Question 4e CP-factor predictive regressions."""
    results: dict[int, RegressionResult] = {}
    for maturity in REGRESSION_MATURITIES:
        regression_data = pd.DataFrame(
            {
                "dependent": wide[("xr", maturity)].shift(-MONTHS_PER_YEAR),
                "predictor": cp,
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


def format_q4d_table(result: CPResult) -> str:
    """Format the six Question 4d theta estimates as a LaTeX table."""
    rows = []
    for index, coefficient in enumerate(result.coefficients):
        rows.append(f"$\\hat{{\\theta}}_{index}$ & {coefficient:.6f} \\\\")
    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Cochrane--Piazzesi factor coefficients}",
            r"\label{tab:q4d}",
            r"\begin{tabular}{lr}",
            r"\toprule",
            r"Coefficient & Estimate \\",
            r"\midrule",
            *rows,
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.72\textwidth}",
            (
                r"\footnotesize\textit{Note:} OLS estimates. "
                f"$R^2={result.r_squared:.3f}$; observations: {result.nobs}."
            ),
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def load_recession_data(source: str) -> pd.DataFrame:
    """Read the official monthly FRED USREC series from a URL or local CSV."""
    recession = pd.read_csv(source)
    required = {"observation_date", "USREC"}
    missing = required.difference(recession.columns)
    if missing:
        raise ValueError(f"USREC data are missing columns: {sorted(missing)}")
    recession = recession.loc[:, ["observation_date", "USREC"]].copy()
    recession["month"] = pd.to_datetime(
        recession["observation_date"], errors="raise"
    ).dt.to_period("M")
    recession["USREC"] = pd.to_numeric(recession["USREC"], errors="raise")
    if not recession["USREC"].isin([0, 1]).all():
        raise ValueError("USREC must contain only zero-one recession indicators.")
    if recession["month"].duplicated().any():
        raise ValueError("USREC contains duplicate monthly observations.")
    return recession.loc[:, ["month", "USREC"]].sort_values("month")


def recession_intervals(
    recession: pd.DataFrame, first_month: pd.Period, last_month: pd.Period
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Convert monthly USREC flags into continuous shaded intervals."""
    sample_months = pd.period_range(first_month, last_month, freq="M")
    flags = recession.set_index("month")["USREC"].reindex(sample_months)
    if flags.isna().any():
        missing = [str(month) for month in flags.index[flags.isna()][:5]]
        raise ValueError(f"USREC does not cover the bond sample: {missing}")

    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start: pd.Period | None = None
    for month, flag in flags.items():
        if flag == 1 and start is None:
            start = month
        if flag == 0 and start is not None:
            intervals.append((start.to_timestamp(), month.to_timestamp()))
            start = None
    if start is not None:
        intervals.append(
            (start.to_timestamp(), (last_month + 1).to_timestamp())
        )
    return intervals


def create_cp_plot(cp: pd.Series, recession: pd.DataFrame, output: Path) -> None:
    """Create the Question 4d CP plot with NBER recession shading."""
    periods = pd.PeriodIndex(cp.index, freq="M")
    dates = periods.to_timestamp()
    intervals = recession_intervals(recession, periods.min(), periods.max())

    fig, ax = plt.subplots(figsize=(10.0, 5.2))
    for start, end in intervals:
        ax.axvspan(start, end, color="#B8B8B8", alpha=0.45, linewidth=0, zorder=0)
    line = ax.plot(
        dates,
        cp.to_numpy(),
        color="#1F4E79",
        linewidth=1.25,
        label="CP factor",
        zorder=2,
    )[0]
    ax.axhline(0.0, color="#555555", linewidth=0.7, zorder=1)
    ax.set_title("Cochrane-Piazzesi Factor", fontsize=13, pad=10)
    ax.set_xlabel("Month")
    ax.set_ylabel(r"$cp_t$")
    ax.set_xlim(dates.min(), dates.max())
    ax.grid(axis="y", color="#D9D9D9", linewidth=0.6, alpha=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    recession_patch = Patch(
        facecolor="#B8B8B8", alpha=0.45, label="NBER recession"
    )
    ax.legend(handles=[line, recession_patch], frameon=False, loc="best")
    fig.text(
        0.99,
        0.01,
        "Recession indicator: FRED USREC (NBER based).",
        ha="right",
        va="bottom",
        fontsize=8,
        color="#555555",
    )
    fig.tight_layout(rect=(0.0, 0.035, 1.0, 1.0))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output,
        format="pdf",
        bbox_inches="tight",
        metadata={"Title": "Cochrane-Piazzesi Factor with NBER Recessions"},
    )
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate Questions 4d and 4e and create the CP factor plot."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--recession-input",
        default=DEFAULT_RECESSION_INPUT,
        help="Official FRED USREC CSV URL or a local CSV with the same columns.",
    )
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT)
    parser.add_argument("--plot-output", type=Path, default=DEFAULT_PLOT_OUTPUT)
    parser.add_argument(
        "--q4d-table-output", type=Path, default=DEFAULT_Q4D_TABLE_OUTPUT
    )
    parser.add_argument(
        "--q4e-table-output", type=Path, default=DEFAULT_Q4E_TABLE_OUTPUT
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = load_bond_variables(args.input)
    wide = prepare_wide_data(data)
    cp_result, cp = estimate_cp_factor(wide)
    merged = merge_cp(data, cp)
    q4e_results = estimate_q4e(wide, cp)
    recession = load_recession_data(args.recession_input)

    args.data_output.parent.mkdir(parents=True, exist_ok=True)
    args.q4d_table_output.parent.mkdir(parents=True, exist_ok=True)
    args.q4e_table_output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(args.data_output, index=False)
    args.q4d_table_output.write_text(
        format_q4d_table(cp_result), encoding="utf-8"
    )
    q4e_note = (
        "OLS estimates. Brackets contain Bartlett Newey--West (1987) "
        "t-statistics using the automatic bandwidth and VAR(1) prewhitening "
        "procedure in Newey and West (1994), equation (2.2)."
    )
    args.q4e_table_output.write_text(
        format_regression_table(
            q4e_results,
            "One-year excess-return predictability using the "
            "Cochrane--Piazzesi factor",
            "tab:q4e",
            q4e_note,
        ),
        encoding="utf-8",
    )
    create_cp_plot(cp, recession, args.plot_output)

    print("Question 4d coefficients:")
    for index, coefficient in enumerate(cp_result.coefficients):
        print(f"  theta_{index}={coefficient:.8f}")
    print(f"  R2={cp_result.r_squared:.6f}, N={cp_result.nobs}")
    print("Question 4e results:")
    for maturity, result in q4e_results.items():
        print(
            f"  H={maturity}: b={result.slope:.6f}, "
            f"t={result.slope_t_stat:.3f}, R2={result.r_squared:.6f}, "
            f"N={result.nobs}, NW94 n={result.preliminary_lag}, "
            f"L={result.bandwidth}"
        )
    print(
        f"Saved {args.data_output}, {args.plot_output}, "
        f"{args.q4d_table_output}, and {args.q4e_table_output}."
    )


if __name__ == "__main__":
    main()
