"""Construct and validate the monthly momentum signal for Question 3a.

The script applies the sample restrictions in ``spec/q3.md``, constructs each
stock's cumulative return from months tau-12 through tau-1, merges the result
with the Chen-Zimmermann Mom12m signal, and produces the three requested
monthly cross-sectional regression figures.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_CRSP_INPUT = Path("data/Q3/CRSP.csv")
DEFAULT_CZ_INPUT = Path("data/Q3/mom12m_firm_monthly_202510.csv")
DEFAULT_INTERCEPT_OUTPUT = Path("output/q3a_intercepts.pdf")
DEFAULT_SLOPE_OUTPUT = Path("output/q3a_slopes.pdf")
DEFAULT_R2_OUTPUT = Path("output/q3a_r2.pdf")
ANALYSIS_START = pd.Timestamp("1963-06-01")
MOMENTUM_MONTHS = 12
DEFAULT_CHUNK_SIZE = 500_000

CRSP_COLUMNS = [
    "PERMNO",
    "PrimaryExch",
    "USIncFlg",
    "IssuerType",
    "SecurityType",
    "SecuritySubType",
    "ShareType",
    "SICCD",
    "MthCalDt",
    "MthRet",
]
CZ_COLUMNS = ["permno", "yyyymm", "Mom12m"]


def read_and_filter_crsp(path: Path, chunk_size: int) -> pd.DataFrame:
    """Read CRSP in chunks and apply the specified firm-month restrictions."""
    observed_columns = set(pd.read_csv(path, nrows=0).columns)
    missing_columns = set(CRSP_COLUMNS).difference(observed_columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CRSP data are missing required columns: {missing}")

    filtered_chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        usecols=CRSP_COLUMNS,
        chunksize=chunk_size,
        low_memory=False,
    ):
        chunk["PERMNO"] = pd.to_numeric(chunk["PERMNO"], errors="coerce")
        chunk["SICCD"] = pd.to_numeric(chunk["SICCD"], errors="coerce")
        chunk["MthRet"] = pd.to_numeric(chunk["MthRet"], errors="coerce")

        included = (
            chunk["ShareType"].eq("NS")
            & chunk["SecurityType"].eq("EQTY")
            & chunk["SecuritySubType"].eq("COM")
            & chunk["USIncFlg"].eq("Y")
            & chunk["IssuerType"].isin(["ACOR", "CORP"])
            & chunk["PrimaryExch"].isin(["N", "A", "Q"])
            & ~chunk["SICCD"].between(4900, 4949, inclusive="both")
            & ~chunk["SICCD"].between(6000, 6999, inclusive="both")
        )
        filtered_chunks.append(
            chunk.loc[included, ["PERMNO", "MthCalDt", "MthRet"]].copy()
        )

    if not filtered_chunks:
        raise ValueError("CRSP input contains no rows.")
    data = pd.concat(filtered_chunks, ignore_index=True)
    data["month"] = pd.to_datetime(data["MthCalDt"], errors="coerce")

    if data[["PERMNO", "month"]].isna().any().any():
        raise ValueError("Eligible CRSP observations contain missing identifiers or dates.")
    data["PERMNO"] = data["PERMNO"].astype("int64")
    data["month"] = data["month"].dt.to_period("M").dt.to_timestamp()

    duplicate = data.duplicated(["PERMNO", "month"], keep=False)
    duplicate_rows_removed = 0
    if duplicate.any():
        duplicate_data = data.loc[duplicate]
        conflicting = (
            duplicate_data.groupby(["PERMNO", "month"], sort=False)["MthRet"]
            .nunique(dropna=False)
            .gt(1)
        )
        if conflicting.any():
            examples = conflicting[conflicting].index[:5].tolist()
            raise ValueError(
                "CRSP contains conflicting returns for the same PERMNO-month: "
                f"{examples}"
            )
        before = len(data)
        data = data.drop_duplicates(["PERMNO", "month"], keep="first")
        duplicate_rows_removed = before - len(data)

    if (data["MthRet"].dropna() < -1.0).any():
        raise ValueError("CRSP contains a simple monthly return below -100%.")

    data = data.sort_values(["PERMNO", "month"], kind="stable").reset_index(
        drop=True
    )
    data.attrs["duplicate_rows_removed"] = duplicate_rows_removed
    return data


def construct_momentum(data: pd.DataFrame) -> pd.DataFrame:
    """Construct MOM from the 12 monthly returns ending one month earlier."""
    result = data.copy()
    group_key = result["PERMNO"]

    # A -100% return makes the cumulative gross return zero. For all other
    # returns, summing log(1 + return) is an efficient and stable product.
    return_is_observed = result["MthRet"].notna().astype("int8")
    return_is_zero_gross = result["MthRet"].eq(-1.0).astype("int8")
    log_gross_return = pd.Series(0.0, index=result.index)
    positive_gross_return = result["MthRet"] > -1.0
    log_gross_return.loc[positive_gross_return] = np.log1p(
        result.loc[positive_gross_return, "MthRet"]
    )

    def rolling_sum(series: pd.Series) -> pd.Series:
        return (
            series.groupby(group_key, sort=False)
            .rolling(MOMENTUM_MONTHS, min_periods=MOMENTUM_MONTHS)
            .sum()
            .reset_index(level=0, drop=True)
        )

    observed_count = rolling_sum(return_is_observed)
    zero_gross_count = rolling_sum(return_is_zero_gross)
    log_gross_sum = rolling_sum(log_gross_return)

    momentum_ending_this_month = np.expm1(log_gross_sum)
    momentum_ending_this_month = momentum_ending_this_month.where(
        zero_gross_count.eq(0), -1.0
    )
    momentum_ending_this_month = momentum_ending_this_month.where(
        observed_count.eq(MOMENTUM_MONTHS)
    )

    # Shift the trailing return window forward one row so month tau excludes
    # its own return and uses tau-12,...,tau-1.
    result["MOM"] = momentum_ending_this_month.groupby(
        group_key, sort=False
    ).shift(1)

    # Twelve prior rows are not sufficient if the stock has a calendar gap.
    # Requiring a 12-month date difference ensures all months tau-12,...,tau
    # are present after the firm-month sample restrictions are applied.
    month_number = result["month"].dt.year * 12 + result["month"].dt.month
    month_t_minus_12 = month_number.groupby(group_key, sort=False).shift(
        MOMENTUM_MONTHS
    )
    consecutive_history = month_number.sub(month_t_minus_12).eq(MOMENTUM_MONTHS)
    result["MOM"] = result["MOM"].where(consecutive_history)

    result["yyyymm"] = result["month"].dt.year * 100 + result["month"].dt.month
    return result.loc[
        result["month"].ge(ANALYSIS_START),
        ["PERMNO", "month", "yyyymm", "MOM"],
    ].reset_index(drop=True)


def read_cz_momentum(path: Path) -> pd.DataFrame:
    """Read and validate the Chen-Zimmermann Mom12m data."""
    observed_columns = set(pd.read_csv(path, nrows=0).columns)
    missing_columns = set(CZ_COLUMNS).difference(observed_columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CZ data are missing required columns: {missing}")

    data = pd.read_csv(path, usecols=CZ_COLUMNS, low_memory=False)
    for column in CZ_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[CZ_COLUMNS].isna().any().any():
        raise ValueError("CZ momentum data contain missing or nonnumeric values.")
    if data.duplicated(["permno", "yyyymm"]).any():
        raise ValueError("CZ momentum data contain duplicate permno-yyyymm rows.")
    data["permno"] = data["permno"].astype("int64")
    data["yyyymm"] = data["yyyymm"].astype("int64")
    return data.rename(columns={"Mom12m": "MOMCZ"})


def merge_momentum(crsp_momentum: pd.DataFrame, cz: pd.DataFrame) -> pd.DataFrame:
    """Merge MOM with MOMCZ and keep observations where both are available."""
    merged = crsp_momentum.merge(
        cz,
        left_on=["PERMNO", "yyyymm"],
        right_on=["permno", "yyyymm"],
        how="inner",
        validate="one_to_one",
    )
    merged = merged.dropna(subset=["MOM", "MOMCZ"]).copy()
    if merged.empty:
        raise ValueError("The CRSP-CZ merge contains no usable momentum observations.")
    if not np.isfinite(merged[["MOM", "MOMCZ"]].to_numpy()).all():
        raise ValueError("The merged momentum sample contains non-finite values.")
    return merged


def estimate_monthly_regressions(data: pd.DataFrame) -> pd.DataFrame:
    """Estimate MOMCZ on a constant and MOM separately in each month."""
    rows: list[dict[str, float | int | pd.Timestamp]] = []
    for month, monthly in data.groupby("month", sort=True):
        x = monthly["MOM"].to_numpy(dtype="float64")
        y = monthly["MOMCZ"].to_numpy(dtype="float64")
        design = np.column_stack([np.ones(len(monthly)), x])
        coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        if len(monthly) < 3 or rank != 2:
            raise ValueError(
                f"Monthly regression for {month:%Y-%m} lacks usable variation."
            )
        fitted = design @ coefficients
        residual_sum_squares = float(np.square(y - fitted).sum())
        total_sum_squares = float(np.square(y - y.mean()).sum())
        if total_sum_squares <= 0.0:
            raise ValueError(f"MOMCZ has no variation in {month:%Y-%m}.")
        rows.append(
            {
                "month": month,
                "intercept": float(coefficients[0]),
                "slope": float(coefficients[1]),
                "r_squared": 1.0 - residual_sum_squares / total_sum_squares,
                "nobs": len(monthly),
            }
        )
    return pd.DataFrame(rows)


def create_time_series_figure(
    results: pd.DataFrame,
    column: str,
    y_label: str,
    output_path: Path,
) -> None:
    """Create one requested monthly regression time-series figure."""
    figure, axis = plt.subplots(figsize=(8.2, 4.8))
    axis.plot(results["month"], results[column], color="#1f4e79", linewidth=1.0)
    axis.set_xlabel("Month")
    axis.set_ylabel(y_label)
    axis.xaxis.set_major_locator(mdates.YearLocator(base=10))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7)
    axis.margins(x=0)
    figure.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 3a.")
    parser.add_argument("--crsp-input", type=Path, default=DEFAULT_CRSP_INPUT)
    parser.add_argument("--cz-input", type=Path, default=DEFAULT_CZ_INPUT)
    parser.add_argument(
        "--intercept-output", type=Path, default=DEFAULT_INTERCEPT_OUTPUT
    )
    parser.add_argument("--slope-output", type=Path, default=DEFAULT_SLOPE_OUTPUT)
    parser.add_argument("--r2-output", type=Path, default=DEFAULT_R2_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be positive.")

    filtered_crsp = read_and_filter_crsp(args.crsp_input, args.chunk_size)
    duplicate_rows_removed = filtered_crsp.attrs["duplicate_rows_removed"]
    crsp_momentum = construct_momentum(filtered_crsp)
    cz_momentum = read_cz_momentum(args.cz_input)
    merged = merge_momentum(crsp_momentum, cz_momentum)
    results = estimate_monthly_regressions(merged)

    create_time_series_figure(
        results, "intercept", r"Intercept, $a_\tau$", args.intercept_output
    )
    create_time_series_figure(
        results, "slope", r"Slope, $b_\tau$", args.slope_output
    )
    create_time_series_figure(
        results, "r_squared", r"$R^2$", args.r2_output
    )

    print(f"Removed {duplicate_rows_removed:,} exact duplicate CRSP firm-month rows.")
    print(
        f"Merged sample: {len(merged):,} firm-months from "
        f"{merged['month'].min():%Y-%m} through {merged['month'].max():%Y-%m}."
    )
    print(
        f"Estimated {len(results):,} monthly regressions; median N = "
        f"{int(results['nobs'].median()):,}."
    )
    print(
        "Average intercept, slope, and R-squared: "
        f"{results['intercept'].mean():.6f}, "
        f"{results['slope'].mean():.6f}, "
        f"{results['r_squared'].mean():.6f}."
    )
    print(
        "Saved figures to "
        f"{args.intercept_output}, {args.slope_output}, and {args.r2_output}."
    )


if __name__ == "__main__":
    main()
