"""Construct and validate the monthly book-to-market signal for Question 3b.

The script implements the empirical design in ``spec/q3.md``. It constructs
book equity from Compustat, combines it with prior-December CRSP market equity,
holds book-to-market fixed from June through the following May, and compares
the result with the Chen-Zimmermann BMdec signal under both the assignment's
exponential definition and the requested level diagnostic.
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


DEFAULT_CCM_INPUT = Path("data/Q3/CRSP_Compustat.csv")
DEFAULT_CZ_INPUT = Path("data/Q3/bmdec_gp_firm_monthly_202510.csv")
DEFAULT_MAIN_INTERCEPT_OUTPUT = Path("output/q3b_intercepts.pdf")
DEFAULT_MAIN_SLOPE_OUTPUT = Path("output/q3b_slopes.pdf")
DEFAULT_MAIN_R2_OUTPUT = Path("output/q3b_r2.pdf")
DEFAULT_LEVEL_INTERCEPT_OUTPUT = Path("output/q3b_level_intercepts.pdf")
DEFAULT_LEVEL_SLOPE_OUTPUT = Path("output/q3b_level_slopes.pdf")
DEFAULT_LEVEL_R2_OUTPUT = Path("output/q3b_level_r2.pdf")

ANALYSIS_START = pd.Timestamp("1963-06-01")
FIRST_PORTFOLIO_YEAR = 1963
DEFAULT_CHUNK_SIZE = 400_000
FLOAT_EXP_LIMIT = float(np.log(np.finfo("float64").max))
EXPECTED_OVERFLOW_OBSERVATIONS = 6

CCM_COLUMNS = [
    "PERMNO",
    "SICCD",
    "MthCalDt",
    "MthPrc",
    "USIncFlg",
    "IssuerType",
    "SecurityType",
    "SecuritySubType",
    "ShareType",
    "PrimaryExch",
    "ShrOut",
    "gvkey",
    "datadate",
    "curcd",
    "at",
    "ceq",
    "lt",
    "pstk",
    "pstkl",
    "pstkrv",
    "seq",
    "txditc",
]
ACCOUNTING_VALUE_COLUMNS = [
    "at",
    "ceq",
    "lt",
    "pstk",
    "pstkl",
    "pstkrv",
    "seq",
    "txditc",
]
CZ_COLUMNS = ["permno", "yyyymm", "BMdec"]


def sample_restriction_mask(data: pd.DataFrame) -> pd.Series:
    """Return the Question 3 common-stock sample restriction mask."""
    return (
        data["ShareType"].eq("NS")
        & data["SecurityType"].eq("EQTY")
        & data["SecuritySubType"].eq("COM")
        & data["USIncFlg"].eq("Y")
        & data["IssuerType"].isin(["ACOR", "CORP"])
        & data["PrimaryExch"].isin(["N", "A", "Q"])
        & ~data["SICCD"].between(4900, 4949, inclusive="both")
        & ~data["SICCD"].between(6000, 6999, inclusive="both")
    )


def read_ccm_data(
    path: Path, chunk_size: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the large CCM file in chunks and return monthly and annual data."""
    observed_columns = set(pd.read_csv(path, nrows=0).columns)
    missing_columns = set(CCM_COLUMNS).difference(observed_columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CRSP/Compustat data are missing columns: {missing}")

    monthly_chunks: list[pd.DataFrame] = []
    accounting_chunks: list[pd.DataFrame] = []
    numeric_columns = [
        "PERMNO",
        "SICCD",
        "MthPrc",
        "ShrOut",
        *ACCOUNTING_VALUE_COLUMNS,
    ]

    for chunk in pd.read_csv(
        path,
        usecols=CCM_COLUMNS,
        chunksize=chunk_size,
        low_memory=False,
    ):
        for column in numeric_columns:
            chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
        chunk["month"] = pd.to_datetime(
            chunk["MthCalDt"], errors="coerce"
        ).dt.to_period("M").dt.to_timestamp()
        chunk["datadate"] = pd.to_datetime(chunk["datadate"], errors="coerce")

        eligible = sample_restriction_mask(chunk)
        monthly_chunks.append(
            chunk.loc[
                eligible, ["PERMNO", "gvkey", "month", "MthPrc", "ShrOut"]
            ].copy()
        )

        # Compustat history is a firm-level accounting requirement, so retain
        # all USD annual records rather than conditioning prior history on the
        # stock satisfying the CRSP restrictions in those earlier years.
        accounting_chunks.append(
            chunk.loc[
                chunk["curcd"].eq("USD"),
                ["PERMNO", "gvkey", "datadate", *ACCOUNTING_VALUE_COLUMNS],
            ].drop_duplicates()
        )

    if not monthly_chunks or not accounting_chunks:
        raise ValueError("CRSP/Compustat input contains no rows.")

    monthly = pd.concat(monthly_chunks, ignore_index=True)
    accounting = pd.concat(accounting_chunks, ignore_index=True).drop_duplicates()
    return validate_monthly_crsp(monthly), validate_accounting(accounting)


def validate_monthly_crsp(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and deduplicate eligible monthly CRSP observations."""
    if data[["PERMNO", "gvkey", "month"]].isna().any().any():
        raise ValueError(
            "Eligible CRSP rows contain missing PERMNO, gvkey, or dates."
        )
    data = data.copy()
    data["PERMNO"] = data["PERMNO"].astype("int64")

    duplicate = data.duplicated(["PERMNO", "month"], keep=False)
    duplicate_rows_removed = 0
    if duplicate.any():
        duplicated = data.loc[duplicate]
        conflicts = (
            duplicated.groupby(["PERMNO", "month"], sort=False)[
                ["gvkey", "MthPrc", "ShrOut"]
            ]
            .nunique(dropna=False)
            .gt(1)
            .any(axis=1)
        )
        if conflicts.any():
            examples = conflicts[conflicts].index[:5].tolist()
            raise ValueError(
                "CRSP contains conflicting data for PERMNO-month keys: "
                f"{examples}"
            )
        before = len(data)
        data = data.drop_duplicates(["PERMNO", "month"], keep="first")
        duplicate_rows_removed = before - len(data)

    data = data.sort_values(["PERMNO", "month"], kind="stable").reset_index(
        drop=True
    )
    data.attrs["duplicate_rows_removed"] = duplicate_rows_removed
    return data


def validate_accounting(data: pd.DataFrame) -> pd.DataFrame:
    """Validate USD Compustat annual records and their accounting values."""
    if data[["PERMNO", "gvkey", "datadate"]].isna().any().any():
        raise ValueError("USD Compustat rows contain missing identifiers or dates.")
    data = data.copy()
    data["PERMNO"] = data["PERMNO"].astype("int64")

    duplicate = data.duplicated(["gvkey", "datadate"], keep=False)
    if duplicate.any():
        duplicated = data.loc[duplicate]
        conflicts = (
            duplicated.groupby(["gvkey", "datadate"], sort=False)[
                ACCOUNTING_VALUE_COLUMNS
            ]
            .nunique(dropna=False)
            .gt(1)
            .any(axis=1)
        )
        if conflicts.any():
            examples = conflicts[conflicts].index[:5].tolist()
            raise ValueError(
                "Compustat contains conflicting values for gvkey-datadate: "
                f"{examples}"
            )
    return data


def construct_book_equity(accounting: pd.DataFrame) -> pd.DataFrame:
    """Construct eligible annual book equity by Compustat firm."""
    firm_values = accounting[
        ["gvkey", "datadate", *ACCOUNTING_VALUE_COLUMNS]
    ].drop_duplicates(["gvkey", "datadate"], keep="first")
    firm_values["accounting_year"] = firm_values["datadate"].dt.year

    # The user's rule selects the latest fiscal-year end when more than one
    # observation for a firm ends in the same calendar year.
    firm_values = (
        firm_values.sort_values(
            ["gvkey", "accounting_year", "datadate"], kind="stable"
        )
        .drop_duplicates(["gvkey", "accounting_year"], keep="last")
        .reset_index(drop=True)
    )

    available_years = pd.MultiIndex.from_frame(
        firm_values[["gvkey", "accounting_year"]]
    )
    previous_one = pd.MultiIndex.from_arrays(
        [firm_values["gvkey"], firm_values["accounting_year"] - 1]
    )
    previous_two = pd.MultiIndex.from_arrays(
        [firm_values["gvkey"], firm_values["accounting_year"] - 2]
    )
    firm_values["has_previous_one"] = previous_one.isin(available_years)
    firm_values["has_previous_two"] = previous_two.isin(available_years)
    firm_values["portfolio_year"] = firm_values["accounting_year"] + 1

    history_eligible = (
        firm_values["portfolio_year"].eq(FIRST_PORTFOLIO_YEAR)
        & firm_values["accounting_year"].eq(FIRST_PORTFOLIO_YEAR - 1)
    ) | (
        firm_values["portfolio_year"].gt(FIRST_PORTFOLIO_YEAR)
        & firm_values["has_previous_one"]
        & firm_values["has_previous_two"]
    )

    preferred_stock = (
        firm_values["pstkrv"]
        .combine_first(firm_values["pstkl"])
        .combine_first(firm_values["pstk"])
    )
    stockholders_equity = (
        firm_values["seq"]
        .combine_first(firm_values["ceq"] + firm_values["pstk"])
        .combine_first(firm_values["at"] - firm_values["lt"])
    )
    firm_values["BE"] = (
        stockholders_equity
        + firm_values["txditc"].fillna(0.0)
        - preferred_stock
    )
    return firm_values.loc[
        history_eligible & firm_values["BE"].gt(0),
        ["gvkey", "datadate", "accounting_year", "portfolio_year", "BE"],
    ].copy()


def construct_monthly_book_to_market(
    monthly_crsp: pd.DataFrame, book_equity: pd.DataFrame
) -> pd.DataFrame:
    """Combine annual BE with prior-December ME and assign BM June-May."""
    december = monthly_crsp.loc[
        monthly_crsp["month"].dt.month.eq(12),
        ["PERMNO", "month", "MthPrc", "ShrOut"],
    ].copy()
    december["accounting_year"] = december["month"].dt.year
    december["ME"] = december["MthPrc"].abs() * december["ShrOut"]
    december = december.loc[december["ME"].gt(0)]

    # Resolve changes in CCM links using the gvkey observed for each PERMNO at
    # the June formation date, exactly as specified by the user.
    formation = monthly_crsp.loc[
        monthly_crsp["month"].dt.month.eq(6),
        ["PERMNO", "gvkey", "month"],
    ].copy()
    formation["portfolio_year"] = formation["month"].dt.year
    formation["accounting_year"] = formation["portfolio_year"] - 1
    formation = formation.merge(
        book_equity,
        on=["gvkey", "accounting_year", "portfolio_year"],
        how="inner",
        validate="many_to_one",
    )
    formation = formation.merge(
        december[["PERMNO", "accounting_year", "ME"]],
        on=["PERMNO", "accounting_year"],
        how="inner",
        validate="one_to_one",
    )
    formation["BM"] = 1000.0 * formation["BE"] / formation["ME"]
    if not np.isfinite(formation["BM"]).all() or not formation["BM"].gt(0).all():
        raise ValueError("Constructed annual BM values must be positive and finite.")

    monthly = monthly_crsp.loc[
        monthly_crsp["month"].ge(ANALYSIS_START), ["PERMNO", "month"]
    ].copy()
    monthly["portfolio_year"] = np.where(
        monthly["month"].dt.month.ge(6),
        monthly["month"].dt.year,
        monthly["month"].dt.year - 1,
    )
    monthly = monthly.merge(
        formation[["PERMNO", "portfolio_year", "BM"]],
        on=["PERMNO", "portfolio_year"],
        how="inner",
        validate="many_to_one",
    )
    monthly["yyyymm"] = monthly["month"].dt.year * 100 + monthly["month"].dt.month
    return monthly


def read_cz_book_to_market(path: Path) -> pd.DataFrame:
    """Read and validate the CZ BMdec firm-month data."""
    observed_columns = set(pd.read_csv(path, nrows=0).columns)
    missing_columns = set(CZ_COLUMNS).difference(observed_columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CZ data are missing required columns: {missing}")

    data = pd.read_csv(path, usecols=CZ_COLUMNS, low_memory=False)
    for column in CZ_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if data[["permno", "yyyymm"]].isna().any().any():
        raise ValueError("CZ data contain missing or nonnumeric merge keys.")
    if data.duplicated(["permno", "yyyymm"]).any():
        raise ValueError("CZ data contain duplicate permno-yyyymm rows.")
    data["permno"] = data["permno"].astype("int64")
    data["yyyymm"] = data["yyyymm"].astype("int64")
    return data


def merge_with_cz(
    monthly_bm: pd.DataFrame, cz: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return main exponential and diagnostic level comparison samples."""
    merged = monthly_bm.merge(
        cz,
        left_on=["PERMNO", "yyyymm"],
        right_on=["permno", "yyyymm"],
        how="inner",
        validate="one_to_one",
    ).dropna(subset=["BM", "BMdec"])
    if merged.empty:
        raise ValueError("The constructed BM-CZ merge contains no usable rows.")

    level = merged.copy()
    level["BMCZ"] = level["BMdec"]

    overflow = merged["BMdec"].gt(FLOAT_EXP_LIMIT)
    if int(overflow.sum()) != EXPECTED_OVERFLOW_OBSERVATIONS:
        raise ValueError(
            "Expected exactly six matched exp(BMdec) overflows under the "
            f"approved specification, found {int(overflow.sum())}."
        )
    main = merged.loc[~overflow].copy()
    main["BMCZ"] = np.exp(main["BMdec"])
    if not np.isfinite(main[["BM", "BMCZ"]].to_numpy()).all():
        raise ValueError("Main BM comparison still contains non-finite values.")
    if not np.isfinite(level[["BM", "BMCZ"]].to_numpy()).all():
        raise ValueError("Level BM diagnostic contains non-finite values.")
    return main, level


def estimate_monthly_regressions(data: pd.DataFrame) -> pd.DataFrame:
    """Estimate BMCZ on a constant and constructed BM in each month."""
    rows: list[dict[str, float | int | pd.Timestamp]] = []
    for month, monthly in data.groupby("month", sort=True):
        x = monthly["BM"].to_numpy(dtype="float64")
        y = monthly["BMCZ"].to_numpy(dtype="float64")
        design = np.column_stack([np.ones(len(monthly)), x])
        coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        if len(monthly) < 3 or rank != 2:
            raise ValueError(
                f"Monthly regression for {month:%Y-%m} lacks usable variation."
            )
        fitted = design @ coefficients
        residual_sum_squares = float(np.square(y - fitted).sum())
        total_sum_squares = float(np.square(y - y.mean()).sum())
        if total_sum_squares <= 0.0 or not np.isfinite(total_sum_squares):
            raise ValueError(f"BMCZ has unusable variation in {month:%Y-%m}.")
        r_squared = 1.0 - residual_sum_squares / total_sum_squares
        if not np.isfinite(coefficients).all() or not np.isfinite(r_squared):
            raise ValueError(f"Regression results are non-finite in {month:%Y-%m}.")
        rows.append(
            {
                "month": month,
                "intercept": float(coefficients[0]),
                "slope": float(coefficients[1]),
                "r_squared": r_squared,
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
    """Create one monthly regression time-series figure."""
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


def comparison_summary(
    main_results: pd.DataFrame, level_results: pd.DataFrame
) -> pd.DataFrame:
    """Summarize the main-versus-level monthly regression comparison."""
    compared = main_results.merge(
        level_results,
        on="month",
        suffixes=("_exp", "_level"),
        validate="one_to_one",
    )
    rows = []
    for statistic in ["intercept", "slope", "r_squared"]:
        difference = compared[f"{statistic}_exp"] - compared[f"{statistic}_level"]
        rows.append(
            {
                "statistic": statistic,
                "mean_exp": compared[f"{statistic}_exp"].mean(),
                "mean_level": compared[f"{statistic}_level"].mean(),
                "median_exp": compared[f"{statistic}_exp"].median(),
                "median_level": compared[f"{statistic}_level"].median(),
                "median_absolute_difference": difference.abs().median(),
            }
        )
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 3b.")
    parser.add_argument("--ccm-input", type=Path, default=DEFAULT_CCM_INPUT)
    parser.add_argument("--cz-input", type=Path, default=DEFAULT_CZ_INPUT)
    parser.add_argument(
        "--main-intercept-output", type=Path, default=DEFAULT_MAIN_INTERCEPT_OUTPUT
    )
    parser.add_argument(
        "--main-slope-output", type=Path, default=DEFAULT_MAIN_SLOPE_OUTPUT
    )
    parser.add_argument("--main-r2-output", type=Path, default=DEFAULT_MAIN_R2_OUTPUT)
    parser.add_argument(
        "--level-intercept-output", type=Path, default=DEFAULT_LEVEL_INTERCEPT_OUTPUT
    )
    parser.add_argument(
        "--level-slope-output", type=Path, default=DEFAULT_LEVEL_SLOPE_OUTPUT
    )
    parser.add_argument(
        "--level-r2-output", type=Path, default=DEFAULT_LEVEL_R2_OUTPUT
    )
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.chunk_size <= 0:
        raise ValueError("--chunk-size must be positive.")

    monthly_crsp, accounting = read_ccm_data(args.ccm_input, args.chunk_size)
    duplicate_rows_removed = monthly_crsp.attrs["duplicate_rows_removed"]
    book_equity = construct_book_equity(accounting)
    monthly_bm = construct_monthly_book_to_market(monthly_crsp, book_equity)
    cz = read_cz_book_to_market(args.cz_input)
    main_sample, level_sample = merge_with_cz(monthly_bm, cz)
    main_results = estimate_monthly_regressions(main_sample)
    level_results = estimate_monthly_regressions(level_sample)

    create_time_series_figure(
        main_results,
        "intercept",
        r"Intercept, $a_\tau$",
        args.main_intercept_output,
    )
    create_time_series_figure(
        main_results, "slope", r"Slope, $b_\tau$", args.main_slope_output
    )
    create_time_series_figure(
        main_results, "r_squared", r"$R^2$", args.main_r2_output
    )
    create_time_series_figure(
        level_results,
        "intercept",
        r"Intercept, $a_\tau$",
        args.level_intercept_output,
    )
    create_time_series_figure(
        level_results, "slope", r"Slope, $b_\tau$", args.level_slope_output
    )
    create_time_series_figure(
        level_results, "r_squared", r"$R^2$", args.level_r2_output
    )

    summary = comparison_summary(main_results, level_results)
    print(f"Removed {duplicate_rows_removed:,} duplicate CRSP firm-month rows.")
    print(
        f"Level diagnostic sample: {len(level_sample):,} firm-months from "
        f"{level_sample['month'].min():%Y-%m} through "
        f"{level_sample['month'].max():%Y-%m}."
    )
    print(
        f"Main exponential sample: {len(main_sample):,} firm-months after "
        f"excluding {len(level_sample) - len(main_sample):,} approved overflows."
    )
    print(
        f"Estimated {len(main_results):,} main and {len(level_results):,} "
        "diagnostic monthly regressions."
    )
    print(summary.to_string(index=False, float_format=lambda value: f"{value:.6g}"))
    print("Saved the three main and three diagnostic figures.")


if __name__ == "__main__":
    main()
