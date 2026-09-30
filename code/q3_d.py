"""Estimate the Question 3d firm-level Fama-MacBeth regressions.

Signals observed in month tau explain stock excess returns in month tau+1.
The implementation follows ``spec/q3.md`` and reuses the Q3c CRSP universe
and return preparation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from q3_c import (
    DEFAULT_BM_GP_INPUT,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CRSP_INPUT,
    DEFAULT_FF3_INPUT,
    FORMATION_END,
    FORMATION_START,
    prepare_return_data,
    read_crsp,
    read_risk_free_rate,
)


DEFAULT_DURATION_INPUT = Path("data/Q3/Dur.csv")
DEFAULT_TABLE_OUTPUT = Path("output/q3d_fama_macbeth.tex")


@dataclass(frozen=True)
class RegressionSpecification:
    """One monthly cross-sectional regression specification."""

    number: int
    predictors: tuple[str, ...]


SPECIFICATIONS = [
    RegressionSpecification(1, ("QBM",)),
    RegressionSpecification(2, ("QGP",)),
    RegressionSpecification(3, ("QDur",)),
    RegressionSpecification(4, ("QBM", "QGP")),
    RegressionSpecification(5, ("QDur", "QBM")),
    RegressionSpecification(6, ("QDur", "QGP")),
    RegressionSpecification(7, ("QDur", "QBM", "QGP")),
]

COEFFICIENT_COLUMNS = ["Intercept", "QBM", "QGP", "QDur"]


def read_bm_gp_signals(path: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    """Read the two Chen-Zimmermann signals used in Question 3d."""
    required = ["permno", "yyyymm", "BMdec", "GP"]
    observed = set(pd.read_csv(path, nrows=0).columns)
    missing = set(required).difference(observed)
    if missing:
        raise ValueError(f"BM/GP data are missing columns: {sorted(missing)}")

    data = pd.read_csv(path, usecols=required, low_memory=False)
    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[["permno", "yyyymm"]].isna().any().any():
        raise ValueError("BM/GP data contain missing firm-month keys.")
    if data.duplicated(["permno", "yyyymm"]).any():
        raise ValueError("BM/GP data contain duplicate firm-month keys.")

    nonfinite_counts: dict[str, int] = {}
    for column in ["BMdec", "GP"]:
        nonfinite = data[column].notna() & ~np.isfinite(data[column])
        nonfinite_counts[column] = int(nonfinite.sum())
        data.loc[nonfinite, column] = np.nan

    data["permno"] = data["permno"].astype("int64")
    data["yyyymm"] = data["yyyymm"].astype("int64")
    return data, nonfinite_counts


def read_duration(path: Path) -> pd.DataFrame:
    """Read and validate the annual duration signal."""
    required = ["PERMNO", "FF.YEAR", "Dur"]
    observed = set(pd.read_csv(path, nrows=0).columns)
    missing = set(required).difference(observed)
    if missing:
        raise ValueError(f"Duration data are missing columns: {sorted(missing)}")

    data = pd.read_csv(path, usecols=required)
    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data.isna().any().any():
        raise ValueError("Duration data contain missing or nonnumeric values.")
    if not np.isfinite(data["Dur"]).all():
        raise ValueError("Duration data contain non-finite values.")
    if data.duplicated(["PERMNO", "FF.YEAR"]).any():
        raise ValueError("Duration data contain duplicate PERMNO-FF.YEAR keys.")

    data["PERMNO"] = data["PERMNO"].astype("int64")
    data["FF.YEAR"] = data["FF.YEAR"].astype("int64")
    return data


def prepare_analysis_panel(
    crsp: pd.DataFrame,
    bm_gp: pd.DataFrame,
    duration: pd.DataFrame,
    risk_free: pd.DataFrame,
) -> pd.DataFrame:
    """Construct month-tau signals, quantiles, weights, and tau+1 returns."""
    formation = crsp.loc[
        crsp["month"].between(FORMATION_START, FORMATION_END),
        ["PERMNO", "month", "yyyymm", "ME"],
    ].copy()
    formation = formation.merge(
        bm_gp,
        left_on=["PERMNO", "yyyymm"],
        right_on=["permno", "yyyymm"],
        how="left",
        validate="one_to_one",
    ).drop(columns="permno")

    formation["FF.YEAR"] = np.where(
        formation["month"].dt.month.ge(6),
        formation["month"].dt.year,
        formation["month"].dt.year - 1,
    ).astype("int64")
    formation = formation.merge(
        duration,
        on=["PERMNO", "FF.YEAR"],
        how="left",
        validate="many_to_one",
    )

    for signal, quantile in [("BMdec", "QBM"), ("GP", "QGP"), ("Dur", "QDur")]:
        formation[quantile] = formation.groupby("month", sort=False)[signal].rank(
            method="average", pct=True
        )

    returns = prepare_return_data(crsp, risk_free)[
        ["PERMNO", "month", "xR"]
    ].rename(columns={"month": "return_month"})
    formation["return_month"] = formation["month"] + pd.offsets.MonthBegin(1)
    panel = formation.merge(
        returns,
        on=["PERMNO", "return_month"],
        how="inner",
        validate="one_to_one",
    )
    if panel.empty:
        raise ValueError("No signal observations match next-month excess returns.")
    if not np.isfinite(panel["xR"]).all():
        raise ValueError("The matched next-month excess returns are non-finite.")
    return panel


def estimate_one_month(
    monthly: pd.DataFrame,
    predictors: tuple[str, ...],
    method: str,
) -> tuple[np.ndarray, int] | None:
    """Estimate one cross-sectional OLS or market-equity WLS regression."""
    required = ["xR", *predictors]
    if method == "WLS":
        required.append("ME")
    sample = monthly.dropna(subset=required).copy()
    if method == "WLS":
        sample = sample.loc[sample["ME"].gt(0)].copy()

    number_parameters = len(predictors) + 1
    if len(sample) <= number_parameters:
        return None
    dependent = sample["xR"].to_numpy(dtype="float64")
    design = np.column_stack(
        [np.ones(len(sample)), sample.loc[:, predictors].to_numpy(dtype="float64")]
    )
    if not np.isfinite(design).all() or not np.isfinite(dependent).all():
        raise ValueError("A monthly regression contains non-finite inputs.")
    if np.linalg.matrix_rank(design) < number_parameters:
        return None

    if method == "OLS":
        coefficients = np.linalg.lstsq(design, dependent, rcond=None)[0]
    elif method == "WLS":
        weights = sample["ME"].to_numpy(dtype="float64")
        weights = weights / weights.mean()
        root_weights = np.sqrt(weights)
        coefficients = np.linalg.lstsq(
            design * root_weights[:, None], dependent * root_weights, rcond=None
        )[0]
    else:
        raise ValueError(f"Unknown estimation method: {method}")
    if not np.isfinite(coefficients).all():
        raise ValueError("A monthly regression produced non-finite coefficients.")
    return coefficients, len(sample)


def estimate_monthly_regressions(panel: pd.DataFrame) -> pd.DataFrame:
    """Estimate all seven specifications by OLS and WLS in every month."""
    rows: list[dict[str, object]] = []
    for specification in SPECIFICATIONS:
        for method in ["OLS", "WLS"]:
            for month, monthly in panel.groupby("month", sort=True):
                result = estimate_one_month(monthly, specification.predictors, method)
                if result is None:
                    continue
                coefficients, number_observations = result
                row: dict[str, object] = {
                    "method": method,
                    "specification": specification.number,
                    "month": month,
                    "nobs": number_observations,
                    "Intercept": coefficients[0],
                    "QBM": np.nan,
                    "QGP": np.nan,
                    "QDur": np.nan,
                }
                for predictor, coefficient in zip(
                    specification.predictors, coefficients[1:], strict=True
                ):
                    row[predictor] = coefficient
                rows.append(row)

    estimates = pd.DataFrame(rows)
    if estimates.empty:
        raise ValueError("No monthly regressions were estimable.")
    return estimates


def fama_macbeth_summary(monthly_estimates: pd.DataFrame) -> pd.DataFrame:
    """Average monthly coefficients and compute ordinary Fama-MacBeth t-stats."""
    rows: list[dict[str, object]] = []
    for method in ["OLS", "WLS"]:
        for specification in SPECIFICATIONS:
            estimates = monthly_estimates.loc[
                monthly_estimates["method"].eq(method)
                & monthly_estimates["specification"].eq(specification.number)
            ]
            expected = ["Intercept", *specification.predictors]
            if estimates.empty:
                raise ValueError(
                    f"No monthly estimates for {method} specification "
                    f"{specification.number}."
                )
            for coefficient in expected:
                values = estimates[coefficient].dropna().to_numpy(dtype="float64")
                if len(values) < 2:
                    raise ValueError("Too few monthly estimates for Fama-MacBeth inference.")
                average = float(values.mean())
                standard_error = float(values.std(ddof=1) / np.sqrt(len(values)))
                if not np.isfinite(standard_error) or standard_error <= 0:
                    raise ValueError("Invalid Fama-MacBeth standard error.")
                rows.append(
                    {
                        "method": method,
                        "specification": specification.number,
                        "coefficient": coefficient,
                        "estimate": average,
                        "standard_error": standard_error,
                        "t_statistic": average / standard_error,
                        "months": len(values),
                    }
                )
    return pd.DataFrame(rows)


def format_result_cell(
    summary: pd.DataFrame, method: str, specification: int, coefficient: str
) -> str:
    """Format one estimate with its Fama-MacBeth t-statistic in parentheses."""
    row = summary.loc[
        summary["method"].eq(method)
        & summary["specification"].eq(specification)
        & summary["coefficient"].eq(coefficient)
    ]
    if row.empty:
        return r"--"
    if len(row) != 1:
        raise ValueError("Fama-MacBeth summary has duplicate result cells.")
    result = row.iloc[0]
    estimate = float(result["estimate"])
    t_statistic = float(result["t_statistic"])
    if abs(estimate) < 0.00005:
        estimate = 0.0
    if abs(t_statistic) < 0.005:
        t_statistic = 0.0
    return f"{estimate:.4f} ({t_statistic:.2f})"


def format_latex_table(summary: pd.DataFrame, monthly: pd.DataFrame) -> str:
    """Format the 14 requested OLS/WLS specifications as a LaTeX table."""
    rows: list[str] = []
    for method in ["OLS", "WLS"]:
        for specification in SPECIFICATIONS:
            cells = [
                format_result_cell(summary, method, specification.number, coefficient)
                for coefficient in COEFFICIENT_COLUMNS
            ]
            rows.append(
                f"{method} & ({specification.number}) & "
                + " & ".join(cells)
                + r" \\"
            )

    coverage = (
        monthly.groupby(["method", "specification"], sort=False)["month"]
        .agg(["min", "max", "nunique"])
        .reset_index()
    )
    earliest = coverage["min"].min()
    latest = coverage["max"].max()
    minimum_months = int(coverage["nunique"].min())
    maximum_months = int(coverage["nunique"].max())
    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Firm-level Fama--MacBeth regressions}",
            r"\label{tab:q3d_fama_macbeth}",
            r"\begin{tabular}{llrrrr}",
            r"\toprule",
            (
                r"Method & Specification & Intercept & $Q^{BM}$ & "
                r"$Q^{GP}$ & $Q^{Dur}$ \\"
            ),
            r"\midrule",
            *rows[:7],
            r"\midrule",
            *rows[7:],
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.98\textwidth}",
            (
                r"\footnotesize\textit{Note:} Each cell reports the time-series "
                r"average of the monthly cross-sectional coefficient, with its "
                r"ordinary Fama--MacBeth $t$-statistic in parentheses. Signal "
                r"quantiles are monthly percentile ranks calculated using average "
                r"ranks for ties. Signals dated $\tau$ explain excess returns dated "
                r"$\tau+1$. WLS uses market equity dated $\tau$. Each specification "
                r"uses its own complete-case sample. Coefficients are in decimal "
                r"monthly-return units. Monthly regression coverage ranges from "
                f"{earliest:%B %Y} through {latest:%B %Y}; specifications contain "
                f"between {minimum_months} and {maximum_months} monthly estimates."
            ),
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 3d.")
    parser.add_argument("--crsp-input", type=Path, default=DEFAULT_CRSP_INPUT)
    parser.add_argument("--bm-gp-input", type=Path, default=DEFAULT_BM_GP_INPUT)
    parser.add_argument("--duration-input", type=Path, default=DEFAULT_DURATION_INPUT)
    parser.add_argument("--ff3-input", type=Path, default=DEFAULT_FF3_INPUT)
    parser.add_argument("--table-output", type=Path, default=DEFAULT_TABLE_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be positive.")

    crsp = read_crsp(args.crsp_input, args.chunk_size)
    duplicate_rows_removed = crsp.attrs["duplicate_rows_removed"]
    bm_gp, nonfinite_counts = read_bm_gp_signals(args.bm_gp_input)
    duration = read_duration(args.duration_input)
    risk_free = read_risk_free_rate(args.ff3_input)
    panel = prepare_analysis_panel(crsp, bm_gp, duration, risk_free)
    monthly = estimate_monthly_regressions(panel)
    summary = fama_macbeth_summary(monthly)

    args.table_output.parent.mkdir(parents=True, exist_ok=True)
    args.table_output.write_text(
        format_latex_table(summary, monthly), encoding="utf-8"
    )

    coverage = (
        monthly.groupby(["method", "specification"], sort=True)
        .agg(
            start=("month", "min"),
            end=("month", "max"),
            months=("month", "nunique"),
            median_firms=("nobs", "median"),
            minimum_firms=("nobs", "min"),
        )
        .reset_index()
    )
    print(f"Removed {duplicate_rows_removed:,} duplicate CRSP firm-month rows.")
    print(f"Non-finite BMdec values treated as missing: {nonfinite_counts['BMdec']:,}")
    print(f"Non-finite GP values treated as missing: {nonfinite_counts['GP']:,}")
    print(
        "Formation window: "
        f"{FORMATION_START:%Y-%m} through {FORMATION_END:%Y-%m}."
    )
    print("Monthly regression coverage:")
    print(coverage.to_string(index=False))
    print("Fama-MacBeth estimates:")
    print(
        summary.to_string(
            index=False,
            float_format=lambda value: f"{value:.6f}",
        )
    )
    print(f"Saved LaTeX table to {args.table_output}.")


if __name__ == "__main__":
    main()
