"""Construct the Question 3c Chen-Zimmermann decile portfolios.

The implementation follows ``spec/q3.md``. Signals observed in month tau
earn returns in month tau+1. Annual portfolios use June signals and retain
their assignments from July through the following June. Value-weighted
returns use market equity from the month before the return month.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_CRSP_INPUT = Path("data/Q3/CRSP.csv")
DEFAULT_BM_GP_INPUT = Path("data/Q3/bmdec_gp_firm_monthly_202510.csv")
DEFAULT_MOM_INPUT = Path("data/Q3/mom12m_firm_monthly_202510.csv")
DEFAULT_FF3_INPUT = Path("data/Q3/ff3.csv")
DEFAULT_TABLE_OUTPUT = Path("output/q3c_hml_results.tex")
DEFAULT_CHUNK_SIZE = 500_000

FORMATION_START = pd.Timestamp("1963-06-01")
FORMATION_END = pd.Timestamp("2024-12-01")

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
    "MthPrc",
    "MthRet",
    "ShrOut",
]

SIGNAL_LABELS = {
    "BMCZ": r"$BM^{CZ}$",
    "MOMCZ": r"$MOM^{CZ}$",
    "GPCZ": r"$GP^{CZ}$",
}
SIGNAL_COLORS = {"BMCZ": "blue", "MOMCZ": "red", "GPCZ": "green"}
SIGNAL_MARKERS = {"BMCZ": "o", "MOMCZ": "s", "GPCZ": "^"}


@dataclass(frozen=True)
class PortfolioSpecification:
    """Definition of one requested portfolio construction."""

    slug: str
    label: str
    annual: bool
    nyse_breakpoints: bool
    value_weighted: bool
    output: Path


PORTFOLIO_SPECIFICATIONS = [
    PortfolioSpecification(
        "vw_annual_nyse",
        "Value-weighted, annual, NYSE breakpoints",
        True,
        True,
        True,
        Path("output/q3c_vw_annual_nyse.pdf"),
    ),
    PortfolioSpecification(
        "ew_annual_nyse",
        "Equal-weighted, annual, NYSE breakpoints",
        True,
        True,
        False,
        Path("output/q3c_ew_annual_nyse.pdf"),
    ),
    PortfolioSpecification(
        "vw_monthly_nyse",
        "Value-weighted, monthly, NYSE breakpoints",
        False,
        True,
        True,
        Path("output/q3c_vw_monthly_nyse.pdf"),
    ),
    PortfolioSpecification(
        "vw_annual_general",
        "Value-weighted, annual, general breakpoints",
        True,
        False,
        True,
        Path("output/q3c_vw_annual_general.pdf"),
    ),
    PortfolioSpecification(
        "ew_monthly_general",
        "Equal-weighted, monthly, general breakpoints",
        False,
        False,
        False,
        Path("output/q3c_ew_monthly_general.pdf"),
    ),
]


def sample_restriction_mask(data: pd.DataFrame) -> pd.Series:
    """Return the specified CRSP common-stock universe mask."""
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


def read_crsp(path: Path, chunk_size: int) -> pd.DataFrame:
    """Read CRSP in chunks, apply restrictions, and construct market equity."""
    observed_columns = set(pd.read_csv(path, nrows=0).columns)
    missing_columns = set(CRSP_COLUMNS).difference(observed_columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CRSP data are missing required columns: {missing}")

    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        path,
        usecols=CRSP_COLUMNS,
        chunksize=chunk_size,
        low_memory=False,
    ):
        for column in ["PERMNO", "SICCD", "MthPrc", "MthRet", "ShrOut"]:
            chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
        chunk["month"] = pd.to_datetime(
            chunk["MthCalDt"], errors="coerce"
        ).dt.to_period("M").dt.to_timestamp()
        eligible = sample_restriction_mask(chunk)
        selected = chunk.loc[
            eligible,
            ["PERMNO", "PrimaryExch", "month", "MthPrc", "MthRet", "ShrOut"],
        ].copy()
        selected["ME"] = selected["MthPrc"].abs() * selected["ShrOut"]
        selected.loc[~selected["ME"].gt(0), "ME"] = np.nan
        chunks.append(selected)

    if not chunks:
        raise ValueError("CRSP input contains no rows.")
    data = pd.concat(chunks, ignore_index=True)
    if data[["PERMNO", "month"]].isna().any().any():
        raise ValueError("Eligible CRSP rows contain missing identifiers or dates.")
    data["PERMNO"] = data["PERMNO"].astype("int64")

    duplicate = data.duplicated(["PERMNO", "month"], keep=False)
    duplicate_rows_removed = 0
    if duplicate.any():
        duplicated = data.loc[duplicate]
        conflicts = (
            duplicated.groupby(["PERMNO", "month"], sort=False)[
                ["PrimaryExch", "MthRet", "ME"]
            ]
            .nunique(dropna=False)
            .gt(1)
            .any(axis=1)
        )
        if conflicts.any():
            examples = conflicts[conflicts].index[:5].tolist()
            raise ValueError(
                "CRSP contains conflicting eligible PERMNO-month rows: "
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
    group = data.groupby("PERMNO", sort=False)
    month_number = data["month"].dt.year * 12 + data["month"].dt.month
    previous_month_number = group["month"].shift(1)
    previous_month_number = (
        previous_month_number.dt.year * 12 + previous_month_number.dt.month
    )
    data["lagged_ME"] = group["ME"].shift(1).where(
        month_number.sub(previous_month_number).eq(1)
    )
    data["yyyymm"] = data["month"].dt.year * 100 + data["month"].dt.month
    data.attrs["duplicate_rows_removed"] = duplicate_rows_removed
    return data


def read_risk_free_rate(path: Path) -> pd.DataFrame:
    """Read the monthly Fama-French risk-free rate and convert it to decimal."""
    data = pd.read_csv(path)
    if data.shape[1] < 2 or "RF" not in data.columns:
        raise ValueError("Fama-French data must contain a date column and RF.")
    date_column = data.columns[0]
    data["yyyymm"] = pd.to_numeric(data[date_column], errors="coerce")
    data["RF"] = pd.to_numeric(data["RF"], errors="coerce") / 100.0
    data = data.dropna(subset=["yyyymm", "RF"]).copy()
    data["yyyymm"] = data["yyyymm"].astype("int64")
    if data.duplicated("yyyymm").any():
        raise ValueError("Fama-French data contain duplicate months.")
    if not np.isfinite(data["RF"]).all():
        raise ValueError("Fama-French RF contains non-finite values.")
    return data[["yyyymm", "RF"]]


def read_signals(bm_gp_path: Path, mom_path: Path) -> dict[str, pd.DataFrame]:
    """Read and validate the three Chen-Zimmermann signals."""
    bm_gp = pd.read_csv(
        bm_gp_path,
        usecols=["permno", "yyyymm", "BMdec", "GP"],
        low_memory=False,
    )
    momentum = pd.read_csv(
        mom_path,
        usecols=["permno", "yyyymm", "Mom12m"],
        low_memory=False,
    )
    for data, columns, name in [
        (bm_gp, ["permno", "yyyymm", "BMdec", "GP"], "BM/GP"),
        (momentum, ["permno", "yyyymm", "Mom12m"], "momentum"),
    ]:
        for column in columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")
        if data[["permno", "yyyymm"]].isna().any().any():
            raise ValueError(f"CZ {name} data contain missing merge keys.")
        if data.duplicated(["permno", "yyyymm"]).any():
            raise ValueError(f"CZ {name} data contain duplicate firm-month keys.")
        data["permno"] = data["permno"].astype("int64")
        data["yyyymm"] = data["yyyymm"].astype("int64")

    return {
        "BMCZ": bm_gp[["permno", "yyyymm", "BMdec"]].rename(
            columns={"BMdec": "signal"}
        ),
        "MOMCZ": momentum[["permno", "yyyymm", "Mom12m"]].rename(
            columns={"Mom12m": "signal"}
        ),
        "GPCZ": bm_gp[["permno", "yyyymm", "GP"]].rename(
            columns={"GP": "signal"}
        ),
    }


def prepare_return_data(crsp: pd.DataFrame, risk_free: pd.DataFrame) -> pd.DataFrame:
    """Construct stock excess returns and retain fields needed for portfolios."""
    returns = crsp[
        ["PERMNO", "month", "yyyymm", "MthRet", "lagged_ME"]
    ].merge(risk_free, on="yyyymm", how="left", validate="many_to_one")
    relevant = returns["month"].between(
        FORMATION_START + pd.offsets.MonthBegin(1),
        FORMATION_END + pd.offsets.MonthBegin(1),
    )
    if returns.loc[relevant & returns["MthRet"].notna(), "RF"].isna().any():
        raise ValueError("Risk-free rate is missing for a required return month.")
    returns["xR"] = returns["MthRet"] - returns["RF"]
    returns = returns.dropna(subset=["xR"]).copy()
    if not np.isfinite(returns["xR"]).all():
        raise ValueError("Constructed stock excess returns are non-finite.")
    returns["formation_year"] = np.where(
        returns["month"].dt.month.ge(7),
        returns["month"].dt.year,
        returns["month"].dt.year - 1,
    )
    return returns


def prepare_formation_panel(
    crsp: pd.DataFrame, signal_data: pd.DataFrame
) -> pd.DataFrame:
    """Merge one signal with the eligible CRSP universe at formation."""
    formation = crsp[
        ["PERMNO", "PrimaryExch", "month", "yyyymm"]
    ].merge(
        signal_data,
        left_on=["PERMNO", "yyyymm"],
        right_on=["permno", "yyyymm"],
        how="inner",
        validate="one_to_one",
    )
    in_window = formation["month"].between(FORMATION_START, FORMATION_END)
    finite_signal = np.isfinite(formation["signal"])
    nonfinite_signals_removed = int(
        (in_window & formation["signal"].notna() & ~finite_signal).sum()
    )
    formation = formation.loc[in_window & finite_signal].copy()
    if formation.empty:
        raise ValueError("Signal has no observations in the formation sample.")
    result = formation[["PERMNO", "PrimaryExch", "month", "signal"]]
    result.attrs["nonfinite_signals_removed"] = nonfinite_signals_removed
    return result


def assign_deciles(
    formation: pd.DataFrame, annual: bool, nyse_breakpoints: bool
) -> pd.DataFrame:
    """Assign deciles using monthly or June cross-sectional breakpoints."""
    sorting_sample = formation
    if annual:
        sorting_sample = formation.loc[formation["month"].dt.month.eq(6)].copy()
    if sorting_sample.empty:
        raise ValueError("No observations remain for the requested formation timing.")

    assigned_groups: list[pd.DataFrame] = []
    quantiles = np.arange(0.1, 1.0, 0.1)
    for month, monthly in sorting_sample.groupby("month", sort=True):
        breakpoint_sample = monthly
        if nyse_breakpoints:
            breakpoint_sample = monthly.loc[monthly["PrimaryExch"].eq("N")]
        values = breakpoint_sample["signal"].to_numpy(dtype="float64")
        if len(values) < 10 or np.unique(values).size < 10:
            raise ValueError(
                f"Insufficient breakpoint variation in {month:%Y-%m}."
            )
        cutoffs = np.quantile(values, quantiles, method="linear")
        assigned = monthly[["PERMNO", "month"]].rename(
            columns={"month": "formation_month"}
        )
        assigned["decile"] = (
            np.searchsorted(
                cutoffs,
                monthly["signal"].to_numpy(dtype="float64"),
                side="left",
            )
            + 1
        )
        assigned_groups.append(assigned)

    assignments = pd.concat(assigned_groups, ignore_index=True)
    assignments["decile"] = assignments["decile"].astype("int8")
    if annual:
        assignments["formation_year"] = assignments["formation_month"].dt.year
    else:
        assignments["return_month"] = (
            assignments["formation_month"] + pd.offsets.MonthBegin(1)
        )
    return assignments


def calculate_portfolio_returns(
    assignments: pd.DataFrame,
    returns: pd.DataFrame,
    specification: PortfolioSpecification,
) -> pd.DataFrame:
    """Calculate one specification's monthly decile excess returns."""
    return_fields = returns[
        ["PERMNO", "month", "formation_year", "xR", "lagged_ME"]
    ].rename(columns={"month": "return_month"})
    if specification.annual:
        merged = assignments.merge(
            return_fields,
            on=["PERMNO", "formation_year"],
            how="inner",
            validate="one_to_many",
        )
        holding_start = pd.to_datetime(
            merged["formation_year"].astype(str) + "-07-01"
        )
        holding_end = pd.to_datetime(
            (merged["formation_year"] + 1).astype(str) + "-06-01"
        )
        merged = merged.loc[
            merged["return_month"].between(holding_start, holding_end)
        ].copy()
    else:
        merged = assignments.merge(
            return_fields.drop(columns="formation_year"),
            on=["PERMNO", "return_month"],
            how="inner",
            validate="one_to_one",
        )

    if merged.empty:
        raise ValueError(f"No returns remain for {specification.label}.")
    merged["month"] = merged["return_month"]
    if specification.value_weighted:
        merged = merged.loc[merged["lagged_ME"].gt(0)].copy()
        merged["weighted_xR"] = merged["lagged_ME"] * merged["xR"]
        grouped = merged.groupby(["month", "decile"], sort=True, observed=True)
        totals = grouped[["weighted_xR", "lagged_ME"]].sum()
        portfolio_returns = (totals["weighted_xR"] / totals["lagged_ME"]).rename(
            "portfolio_xR"
        )
        counts = grouped.size().rename("n_stocks")
        result = pd.concat([portfolio_returns, counts], axis=1).reset_index()
    else:
        result = (
            merged.groupby(["month", "decile"], sort=True, observed=True)["xR"]
            .agg(portfolio_xR="mean", n_stocks="size")
            .reset_index()
        )
    if not np.isfinite(result["portfolio_xR"]).all():
        raise ValueError(f"Non-finite portfolio return for {specification.label}.")
    return result


def newey_west_mean_inference(values: pd.Series) -> dict[str, float | int]:
    """Estimate a mean and its Q2(b) automatic-bandwidth NW t-statistic."""
    sample = values.dropna().to_numpy(dtype="float64")
    sample_size = len(sample)
    if sample_size < 10 or not np.isfinite(sample).all():
        raise ValueError("HML series is too short or non-finite for inference.")
    mean = float(sample.mean())
    residuals = sample - mean

    preliminary_lag = int(np.floor(4.0 * (sample_size / 100.0) ** (2.0 / 9.0)))
    preliminary_lag = min(preliminary_lag, sample_size - 1)
    plug_in_autocovariances: list[float] = []
    for lag in range(preliminary_lag + 1):
        if lag == 0:
            cross_product = float(residuals @ residuals)
        else:
            cross_product = float(residuals[lag:] @ residuals[:-lag])
        plug_in_autocovariances.append(cross_product / sample_size)

    s_zero = plug_in_autocovariances[0] + 2.0 * sum(
        plug_in_autocovariances[1:]
    )
    s_one = 2.0 * sum(
        lag * plug_in_autocovariances[lag]
        for lag in range(1, preliminary_lag + 1)
    )
    if not np.isfinite(s_zero) or abs(s_zero) <= np.finfo(float).tiny:
        raise ValueError("Newey-West plug-in denominator is zero or non-finite.")
    plug_in_constant = 1.1447 * ((s_one / s_zero) ** 2) ** (1.0 / 3.0)
    raw_bandwidth = plug_in_constant * sample_size ** (1.0 / 3.0)
    bandwidth = max(0, min(int(np.floor(raw_bandwidth)), sample_size - 1))

    spectral_density = float(residuals @ residuals) / sample_size
    for lag in range(1, bandwidth + 1):
        autocovariance = float(residuals[lag:] @ residuals[:-lag]) / (
            sample_size - lag
        )
        weight = 1.0 - lag / (bandwidth + 1.0)
        spectral_density += 2.0 * weight * autocovariance
    variance = spectral_density / sample_size
    if not np.isfinite(variance) or variance <= 0.0:
        raise ValueError("Newey-West variance of the HML mean is not positive.")
    standard_error = float(np.sqrt(variance))
    return {
        "mean": mean,
        "standard_error": standard_error,
        "t_statistic": mean / standard_error,
        "nobs": sample_size,
        "bandwidth": bandwidth,
        "preliminary_lag": preliminary_lag,
        "plug_in_constant": plug_in_constant,
        "raw_bandwidth": raw_bandwidth,
    }


def summarize_portfolios(
    portfolio_returns: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.Series, dict[str, float | int]]:
    """Return average decile returns, monthly HML, and HML inference."""
    averages = (
        portfolio_returns.groupby("decile", observed=True)["portfolio_xR"]
        .mean()
        .reindex(range(1, 11))
    )
    if averages.isna().any():
        missing = averages[averages.isna()].index.tolist()
        raise ValueError(f"Average returns are missing for deciles: {missing}")
    wide = portfolio_returns.pivot(
        index="month", columns="decile", values="portfolio_xR"
    )
    if 1 not in wide.columns or 10 not in wide.columns:
        raise ValueError("Cannot construct HML because decile 1 or 10 is missing.")
    hml = (wide[10] - wide[1]).dropna().rename("HML")
    inference = newey_west_mean_inference(hml)
    return averages.rename("average_xR").reset_index(), hml, inference


def create_scatterplot(
    averages_by_signal: dict[str, pd.DataFrame],
    specification: PortfolioSpecification,
) -> None:
    """Create one requested three-signal decile-return scatterplot."""
    figure, axis = plt.subplots(figsize=(7.4, 4.8))
    for signal in ["BMCZ", "MOMCZ", "GPCZ"]:
        averages = averages_by_signal[signal]
        axis.scatter(
            averages["decile"],
            averages["average_xR"],
            color=SIGNAL_COLORS[signal],
            marker=SIGNAL_MARKERS[signal],
            s=40,
            label=SIGNAL_LABELS[signal],
        )
    axis.axhline(0.0, color="#808080", linewidth=0.8)
    axis.set_xticks(range(1, 11))
    axis.set_xlabel("Decile")
    axis.set_ylabel("Average monthly excess return")
    axis.set_title(specification.label)
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7)
    axis.legend(frameon=False, ncol=3)
    figure.tight_layout()
    specification.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(specification.output, format="pdf", bbox_inches="tight")
    plt.close(figure)


def format_latex_table(results: pd.DataFrame) -> str:
    """Format the 15 requested HML estimates as a LaTeX table."""
    rows = []
    for row in results.itertuples(index=False):
        signal_label = {
            "BMCZ": r"$BM^{CZ}$",
            "MOMCZ": r"$MOM^{CZ}$",
            "GPCZ": r"$GP^{CZ}$",
        }[row.signal]
        rows.append(
            f"{signal_label} & {row.portfolio_type} & "
            f"{row.average_hml * 100:.2f}" + r"\% & "
            f"{row.t_statistic:.2f} \\\\"
        )
    bandwidths = results["bandwidth"].astype(int)
    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{High-minus-low excess returns for Chen--Zimmermann signals}",
            r"\label{tab:q3c_hml_results}",
            r"\begin{tabular}{llrr}",
            r"\toprule",
            r"Signal & Portfolio construction & Average HML (\%) & $t$-statistic \\",
            r"\midrule",
            *rows,
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.96\textwidth}",
            (
                r"\footnotesize\textit{Note:} HML is the monthly excess return "
                r"of decile 10 minus decile 1. Signals observed in month $\tau$ "
                r"earn returns in month $\tau+1$. Value-weighted returns use "
                r"market equity from the previous month. The $t$-statistics use "
                r"the Newey--West (1987, 1994) automatic-bandwidth procedure "
                r"without prewhitening or recoloring. Selected bandwidths range "
                f"from $L={bandwidths.min()}$ to $L={bandwidths.max()}$. "
                r"Average HML returns are reported as monthly percentages."
            ),
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 3c.")
    parser.add_argument("--crsp-input", type=Path, default=DEFAULT_CRSP_INPUT)
    parser.add_argument("--bm-gp-input", type=Path, default=DEFAULT_BM_GP_INPUT)
    parser.add_argument("--mom-input", type=Path, default=DEFAULT_MOM_INPUT)
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
    risk_free = read_risk_free_rate(args.ff3_input)
    returns = prepare_return_data(crsp, risk_free)
    signals = read_signals(args.bm_gp_input, args.mom_input)

    averages_for_plot: dict[str, dict[str, pd.DataFrame]] = {
        specification.slug: {} for specification in PORTFOLIO_SPECIFICATIONS
    }
    hml_rows: list[dict[str, float | int | str]] = []
    coverage_rows: list[dict[str, object]] = []
    nonfinite_rows: list[dict[str, object]] = []
    for signal, signal_data in signals.items():
        formation = prepare_formation_panel(crsp, signal_data)
        nonfinite_rows.append(
            {
                "signal": signal,
                "nonfinite_signals_removed": formation.attrs[
                    "nonfinite_signals_removed"
                ],
            }
        )
        annual_assignments: dict[bool, pd.DataFrame] = {}
        monthly_assignments: dict[bool, pd.DataFrame] = {}
        for nyse_breakpoints in [True, False]:
            annual_assignments[nyse_breakpoints] = assign_deciles(
                formation, annual=True, nyse_breakpoints=nyse_breakpoints
            )
            monthly_assignments[nyse_breakpoints] = assign_deciles(
                formation, annual=False, nyse_breakpoints=nyse_breakpoints
            )
        for specification in PORTFOLIO_SPECIFICATIONS:
            assignments = (
                annual_assignments[specification.nyse_breakpoints]
                if specification.annual
                else monthly_assignments[specification.nyse_breakpoints]
            )
            portfolio_returns = calculate_portfolio_returns(
                assignments, returns, specification
            )
            averages, hml, inference = summarize_portfolios(portfolio_returns)
            averages_for_plot[specification.slug][signal] = averages
            hml_rows.append(
                {
                    "signal": signal,
                    "portfolio_type": specification.label,
                    "average_hml": inference["mean"],
                    "t_statistic": inference["t_statistic"],
                    "standard_error": inference["standard_error"],
                    "nobs": inference["nobs"],
                    "bandwidth": inference["bandwidth"],
                    "preliminary_lag": inference["preliminary_lag"],
                    "plug_in_constant": inference["plug_in_constant"],
                    "raw_bandwidth": inference["raw_bandwidth"],
                }
            )
            coverage_rows.append(
                {
                    "signal": signal,
                    "portfolio_type": specification.slug,
                    "start": hml.index.min(),
                    "end": hml.index.max(),
                    "months": len(hml),
                }
            )
    for specification in PORTFOLIO_SPECIFICATIONS:
        create_scatterplot(averages_for_plot[specification.slug], specification)

    hml_results = pd.DataFrame(hml_rows)
    if len(hml_results) != 15:
        raise RuntimeError(f"Expected 15 HML results, found {len(hml_results)}.")
    args.table_output.parent.mkdir(parents=True, exist_ok=True)
    args.table_output.write_text(format_latex_table(hml_results), encoding="utf-8")

    print(f"Removed {duplicate_rows_removed:,} duplicate CRSP firm-month rows.")
    print(
        "Signal formation window: "
        f"{FORMATION_START:%Y-%m} through {FORMATION_END:%Y-%m}."
    )
    print("Non-finite formation signals removed:")
    print(pd.DataFrame(nonfinite_rows).to_string(index=False))
    print("HML sample coverage:")
    print(pd.DataFrame(coverage_rows).to_string(index=False))
    print("HML estimates and automatic bandwidths:")
    print(
        hml_results[
            [
                "signal",
                "portfolio_type",
                "average_hml",
                "t_statistic",
                "nobs",
                "bandwidth",
            ]
        ].to_string(index=False, float_format=lambda value: f"{value:.6f}")
    )
    print("Saved five scatterplots and the HML LaTeX table.")


if __name__ == "__main__":
    main()
