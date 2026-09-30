"""Estimate the Question 3e portfolio-level pooled panel regressions.

Annual NYSE-breakpoint portfolios formed in June earn returns from July through
the following June. Monthly portfolio characteristics are arithmetic averages
of the current constituent firms' monthly signal deciles. Signals dated tau
explain portfolio excess returns dated tau+1.
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
    prepare_return_data,
    read_crsp,
    read_risk_free_rate,
)
from q3_d import DEFAULT_DURATION_INPUT, read_bm_gp_signals, read_duration


DEFAULT_TABLE_OUTPUT = Path("output/q3e_portfolio_regressions.tex")
SAMPLE_START = pd.Timestamp("1973-06-01")
SAMPLE_END = pd.Timestamp("2024-12-01")

SIGNALS = {
    "BM": ("BMdec", "DecBM"),
    "GP": ("GP", "DecGP"),
    "Dur": ("Dur", "DecDur"),
}


@dataclass(frozen=True)
class RegressionSpecification:
    """One pooled portfolio regression and its portfolio universe."""

    number: int
    predictors: tuple[str, ...]
    portfolio_signals: tuple[str, ...]


SPECIFICATIONS = [
    RegressionSpecification(1, ("DecBM",), ("BM",)),
    RegressionSpecification(2, ("DecGP",), ("GP",)),
    RegressionSpecification(3, ("DecDur",), ("Dur",)),
    RegressionSpecification(4, ("DecBM", "DecGP"), ("BM", "GP")),
    RegressionSpecification(5, ("DecDur", "DecBM"), ("Dur", "BM")),
    RegressionSpecification(6, ("DecDur", "DecGP"), ("Dur", "GP")),
    RegressionSpecification(
        7, ("DecDur", "DecBM", "DecGP"), ("Dur", "BM", "GP")
    ),
]

COEFFICIENTS = ["Intercept", "DecBM", "DecGP", "DecDur"]


def nyse_deciles(monthly: pd.DataFrame, value_column: str) -> pd.Series:
    """Assign monthly deciles using NYSE breakpoints."""
    result = pd.Series(np.nan, index=monthly.index, dtype="float64")
    quantiles = np.arange(0.1, 1.0, 0.1)
    for month, indices in monthly.groupby("month", sort=True).groups.items():
        group = monthly.loc[indices]
        eligible = group[value_column].notna() & np.isfinite(group[value_column])
        breakpoint = group.loc[eligible & group["PrimaryExch"].eq("N"), value_column]
        if len(breakpoint) < 10 or breakpoint.nunique() < 10:
            raise ValueError(
                f"Insufficient NYSE variation for {value_column} in {month:%Y-%m}."
            )
        cutoffs = np.quantile(
            breakpoint.to_numpy(dtype="float64"), quantiles, method="linear"
        )
        values = group.loc[eligible, value_column].to_numpy(dtype="float64")
        result.loc[group.index[eligible]] = (
            np.searchsorted(cutoffs, values, side="left") + 1
        )
    return result


def prepare_signal_panel(
    crsp: pd.DataFrame, bm_gp: pd.DataFrame, duration: pd.DataFrame
) -> pd.DataFrame:
    """Merge the three signals and calculate their monthly NYSE deciles."""
    panel = crsp.loc[
        crsp["month"].between(SAMPLE_START, SAMPLE_END),
        ["PERMNO", "PrimaryExch", "month", "yyyymm", "ME"],
    ].copy()
    panel = panel.merge(
        bm_gp,
        left_on=["PERMNO", "yyyymm"],
        right_on=["permno", "yyyymm"],
        how="left",
        validate="one_to_one",
    ).drop(columns="permno")
    panel["FF.YEAR"] = np.where(
        panel["month"].dt.month.ge(6),
        panel["month"].dt.year,
        panel["month"].dt.year - 1,
    ).astype("int64")
    panel = panel.merge(
        duration, on=["PERMNO", "FF.YEAR"], how="left", validate="many_to_one"
    )
    for _, (raw_signal, decile) in SIGNALS.items():
        panel[decile] = nyse_deciles(panel, raw_signal)
    panel["formation_year"] = np.where(
        panel["month"].dt.month.ge(6),
        panel["month"].dt.year,
        panel["month"].dt.year - 1,
    ).astype("int64")
    return panel


def annual_assignments(panel: pd.DataFrame) -> pd.DataFrame:
    """Create June portfolio assignments for each of the three signals."""
    june = panel.loc[panel["month"].dt.month.eq(6)].copy()
    assignments: list[pd.DataFrame] = []
    for signal, (_, decile_column) in SIGNALS.items():
        selected = june.dropna(subset=[decile_column]).loc[
            :, ["PERMNO", "formation_year", decile_column]
        ].rename(columns={decile_column: "portfolio_decile"})
        selected["formation_signal"] = signal
        selected["portfolio_decile"] = selected["portfolio_decile"].astype("int8")
        assignments.append(selected)
    result = pd.concat(assignments, ignore_index=True)
    result["portfolio_id"] = (
        result["formation_signal"]
        + "_"
        + result["portfolio_decile"].astype(str)
    )
    if result.duplicated(["PERMNO", "formation_year", "formation_signal"]).any():
        raise ValueError("Annual portfolio assignments are not unique.")
    return result


def build_holdings(panel: pd.DataFrame, assignments: pd.DataFrame) -> pd.DataFrame:
    """Map fixed annual memberships to each monthly signal date."""
    fields = panel[
        ["PERMNO", "month", "formation_year", "DecBM", "DecGP", "DecDur"]
    ]
    holdings = assignments.merge(
        fields,
        on=["PERMNO", "formation_year"],
        how="inner",
        validate="many_to_many",
    )
    start = pd.to_datetime(holdings["formation_year"].astype(str) + "-06-01")
    end = pd.to_datetime(
        (holdings["formation_year"] + 1).astype(str) + "-05-01"
    )
    holdings = holdings.loc[holdings["month"].between(start, end)].copy()
    if holdings.empty:
        raise ValueError("No monthly holdings remain after annual assignment.")
    return holdings


def portfolio_characteristics(holdings: pd.DataFrame) -> pd.DataFrame:
    """Average current signal deciles within every annual-sort portfolio."""
    keys = ["month", "formation_signal", "portfolio_id", "portfolio_decile"]
    result = (
        holdings.groupby(keys, sort=True, observed=True)[
            ["DecBM", "DecGP", "DecDur"]
        ]
        .mean()
        .reset_index()
    )
    return result


def portfolio_returns(
    holdings: pd.DataFrame, returns: pd.DataFrame, value_weighted: bool
) -> pd.DataFrame:
    """Calculate tau+1 portfolio excess returns from tau memberships."""
    matched = holdings[
        ["PERMNO", "month", "formation_signal", "portfolio_id", "portfolio_decile"]
    ].copy()
    matched["return_month"] = matched["month"] + pd.offsets.MonthBegin(1)
    return_fields = returns[["PERMNO", "month", "xR", "lagged_ME"]].rename(
        columns={"month": "return_month"}
    )
    matched = matched.merge(
        return_fields,
        on=["PERMNO", "return_month"],
        how="inner",
        validate="many_to_one",
    )
    keys = ["month", "formation_signal", "portfolio_id", "portfolio_decile"]
    if value_weighted:
        matched = matched.loc[matched["lagged_ME"].gt(0)].copy()
        matched["weighted_xR"] = matched["lagged_ME"] * matched["xR"]
        totals = matched.groupby(keys, sort=True, observed=True)[
            ["weighted_xR", "lagged_ME"]
        ].sum()
        result = (totals["weighted_xR"] / totals["lagged_ME"]).rename("xR")
    else:
        result = matched.groupby(keys, sort=True, observed=True)["xR"].mean()
    frame = result.reset_index()
    if frame.empty or not np.isfinite(frame["xR"]).all():
        raise ValueError("Portfolio return construction produced invalid results.")
    return frame


def build_portfolio_panels(
    signal_panel: pd.DataFrame,
    assignments: pd.DataFrame,
    returns: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Create complete VW and EW portfolio-month panels."""
    holdings = build_holdings(signal_panel, assignments)
    characteristics = portfolio_characteristics(holdings)
    expected_months = pd.date_range(SAMPLE_START, SAMPLE_END, freq="MS")
    panels: dict[str, pd.DataFrame] = {}
    for method, value_weighted in [("VW", True), ("EW", False)]:
        panel = portfolio_returns(holdings, returns, value_weighted).merge(
            characteristics,
            on=["month", "formation_signal", "portfolio_id", "portfolio_decile"],
            how="inner",
            validate="one_to_one",
        )
        panel = panel.dropna(subset=["xR", "DecBM", "DecGP", "DecDur"]).copy()
        counts = panel.groupby("month")["portfolio_id"].nunique()
        observed_months = pd.DatetimeIndex(counts.index)
        if not observed_months.equals(expected_months):
            missing = expected_months.difference(observed_months)
            raise ValueError(f"{method} portfolio panel misses months: {list(missing)}")
        if not counts.eq(30).all():
            bad = counts.loc[~counts.eq(30)].head().to_dict()
            raise ValueError(f"{method} does not contain 30 portfolios per month: {bad}")
        panels[method] = panel.sort_values(
            ["month", "formation_signal", "portfolio_decile"], kind="stable"
        ).reset_index(drop=True)
    return panels


def automatic_bandwidth(score_series: np.ndarray) -> tuple[int, int, float, float]:
    """Apply the Q2(b) no-prewhitening Newey-West (1994) plug-in rule."""
    scores = np.asarray(score_series, dtype="float64")
    sample_size = len(scores)
    if sample_size < 10 or not np.isfinite(scores).all():
        raise ValueError("Monthly score series is too short or non-finite.")
    preliminary_lag = int(np.floor(4.0 * (sample_size / 100.0) ** (2.0 / 9.0)))
    preliminary_lag = min(preliminary_lag, sample_size - 1)
    autocovariances = []
    for lag in range(preliminary_lag + 1):
        cross_product = (
            scores @ scores
            if lag == 0
            else scores[lag:] @ scores[:-lag]
        )
        autocovariances.append(float(cross_product) / sample_size)
    s_zero = autocovariances[0] + 2.0 * sum(autocovariances[1:])
    s_one = 2.0 * sum(
        lag * autocovariances[lag]
        for lag in range(1, preliminary_lag + 1)
    )
    if not np.isfinite(s_zero) or abs(s_zero) <= np.finfo(float).tiny:
        raise ValueError("Automatic-bandwidth denominator is zero or non-finite.")
    constant = 1.1447 * ((s_one / s_zero) ** 2) ** (1.0 / 3.0)
    raw_bandwidth = constant * sample_size ** (1.0 / 3.0)
    bandwidth = max(0, min(int(np.floor(raw_bandwidth)), sample_size - 1))
    return bandwidth, preliminary_lag, constant, raw_bandwidth


def driscoll_kraay_covariance(
    design: np.ndarray,
    residuals: np.ndarray,
    months: pd.Series,
) -> tuple[np.ndarray, int, int, float, float]:
    """Calculate Bartlett Driscoll-Kraay covariance using monthly score sums."""
    observation_scores = design * residuals[:, None]
    score_frame = pd.DataFrame(observation_scores)
    score_frame["month"] = pd.to_datetime(months).to_numpy()
    monthly_scores = score_frame.groupby("month", sort=True).sum().to_numpy()
    slope_score = monthly_scores[:, 1:].sum(axis=1)
    bandwidth, preliminary_lag, constant, raw_bandwidth = automatic_bandwidth(
        slope_score
    )
    meat = monthly_scores.T @ monthly_scores
    for lag in range(1, bandwidth + 1):
        cross = monthly_scores[lag:].T @ monthly_scores[:-lag]
        weight = 1.0 - lag / (bandwidth + 1.0)
        meat += weight * (cross + cross.T)
    bread = np.linalg.inv(design.T @ design)
    covariance = bread @ meat @ bread
    covariance = (covariance + covariance.T) / 2.0
    return covariance, bandwidth, preliminary_lag, constant, raw_bandwidth


def estimate_regression(
    panel: pd.DataFrame,
    specification: RegressionSpecification,
) -> dict[str, object]:
    """Estimate one pooled OLS regression with Driscoll-Kraay inference."""
    sample = panel.loc[
        panel["formation_signal"].isin(specification.portfolio_signals)
    ].dropna(subset=["xR", *specification.predictors])
    design = np.column_stack(
        [
            np.ones(len(sample)),
            sample.loc[:, specification.predictors].to_numpy(dtype="float64"),
        ]
    )
    dependent = sample["xR"].to_numpy(dtype="float64")
    coefficients, _, rank, _ = np.linalg.lstsq(design, dependent, rcond=None)
    if rank != design.shape[1]:
        raise ValueError(f"Specification {specification.number} is rank deficient.")
    residuals = dependent - design @ coefficients
    covariance, bandwidth, preliminary_lag, constant, raw_bandwidth = (
        driscoll_kraay_covariance(design, residuals, sample["month"])
    )
    variances = np.diag(covariance)
    if not np.isfinite(variances).all() or (variances <= 0).any():
        raise ValueError(
            f"Specification {specification.number} has invalid DK variances."
        )
    standard_errors = np.sqrt(variances)
    t_statistics = coefficients / standard_errors
    names = ["Intercept", *specification.predictors]
    return {
        "specification": specification.number,
        "coefficients": dict(zip(names, coefficients, strict=True)),
        "t_statistics": dict(zip(names, t_statistics, strict=True)),
        "nobs": len(sample),
        "months": sample["month"].nunique(),
        "bandwidth": bandwidth,
        "preliminary_lag": preliminary_lag,
        "constant": constant,
        "raw_bandwidth": raw_bandwidth,
    }


def estimate_all(panels: dict[str, pd.DataFrame]) -> dict[str, list[dict[str, object]]]:
    """Estimate all 14 requested regressions."""
    results: dict[str, list[dict[str, object]]] = {}
    for method, panel in panels.items():
        method_results = [estimate_regression(panel, spec) for spec in SPECIFICATIONS]
        if any(int(result["months"]) != 619 for result in method_results):
            raise ValueError(f"{method} regressions do not all use 619 months.")
        results[method] = method_results
    return results


def significance_stars(t_statistic: float) -> str:
    """Apply the user-specified two-sided asymptotic-normal cutoffs."""
    magnitude = abs(t_statistic)
    if magnitude >= 2.576:
        return "^{***}"
    if magnitude >= 1.96:
        return "^{**}"
    if magnitude >= 1.645:
        return "^{*}"
    return ""


def result_cell(result: dict[str, object], coefficient: str) -> str:
    """Format one coefficient and its Driscoll-Kraay t-statistic."""
    coefficients = result["coefficients"]
    t_statistics = result["t_statistics"]
    if coefficient not in coefficients:
        return ""
    estimate = float(coefficients[coefficient])
    t_statistic = float(t_statistics[coefficient])
    if abs(estimate) < 0.00005:
        estimate = 0.0
    if abs(t_statistic) < 0.005:
        t_statistic = 0.0
    stars = significance_stars(t_statistic)
    return rf"\shortstack{{$${estimate:.4f}{stars}$$\\$$({t_statistic:.2f})$$}}".replace(
        "$$", "$"
    )


def format_table(results: dict[str, list[dict[str, object]]]) -> str:
    """Create the requested two-panel LaTeX regression table."""
    labels = {
        "Intercept": "Intercept",
        "DecBM": r"$Dec^{BM}$",
        "DecGP": r"$Dec^{GP}$",
        "DecDur": r"$Dec^{Dur}$",
    }
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\caption{Portfolio-level multivariate return regressions}",
        r"\label{tab:q3e_portfolio_regressions}",
        r"\setlength{\tabcolsep}{4pt}",
        r"\renewcommand{\arraystretch}{1.05}",
        r"\begin{tabular}{lccccccc}",
        r"\toprule",
        r" & (1) & (2) & (3) & (4) & (5) & (6) & (7) \\",
        r"\midrule",
    ]
    for method, panel_label in [
        ("VW", "Panel A: Value-weighted portfolios"),
        ("EW", "Panel B: Equal-weighted portfolios"),
    ]:
        if method == "EW":
            lines.append(r"\midrule")
        lines.extend([rf"\multicolumn{{8}}{{l}}{{\textit{{{panel_label}}}}} \\", r"\addlinespace[2pt]"])
        method_results = results[method]
        for coefficient in COEFFICIENTS:
            cells = [result_cell(result, coefficient) for result in method_results]
            lines.append(labels[coefficient] + " & " + " & ".join(cells) + r" \\")
        lines.append(
            "Observations & "
            + " & ".join(str(result["nobs"]) for result in method_results)
            + r" \\"
        )
        lines.append(
            "DK bandwidth & "
            + " & ".join(str(result["bandwidth"]) for result in method_results)
            + r" \\"
        )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.98\textwidth}",
            (
                r"\footnotesize\textit{Note:} Pooled OLS estimates. Parentheses "
                r"contain Driscoll--Kraay $t$-statistics based on monthly pooled "
                r"score sums, Bartlett weights, and the no-prewhitening Newey--West "
                r"(1994) automatic bandwidth rule used in Question 2(b). "
                r"$^{*}$, $^{**}$, and $^{***}$ denote $|t|\geq 1.645$, "
                r"$|t|\geq 1.96$, and $|t|\geq 2.576$, respectively. Portfolios "
                r"are formed each June using NYSE breakpoints. Signals dated "
                r"$\tau$ explain returns dated $\tau+1$. The common signal-month "
                r"sample is June 1973 through December 2024; returns run from July "
                r"1973 through January 2025."
            ),
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crsp", type=Path, default=DEFAULT_CRSP_INPUT)
    parser.add_argument("--bm-gp", type=Path, default=DEFAULT_BM_GP_INPUT)
    parser.add_argument("--duration", type=Path, default=DEFAULT_DURATION_INPUT)
    parser.add_argument("--ff3", type=Path, default=DEFAULT_FF3_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_TABLE_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    crsp = read_crsp(args.crsp, args.chunk_size)
    bm_gp, nonfinite_counts = read_bm_gp_signals(args.bm_gp)
    duration = read_duration(args.duration)
    risk_free = read_risk_free_rate(args.ff3)
    signals = prepare_signal_panel(crsp, bm_gp, duration)
    assignments = annual_assignments(signals)
    returns = prepare_return_data(crsp, risk_free)
    panels = build_portfolio_panels(signals, assignments, returns)
    results = estimate_all(panels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(format_table(results), encoding="utf-8")

    print(f"CRSP exact duplicate rows removed: {crsp.attrs['duplicate_rows_removed']:,}")
    print(f"Non-finite BM values treated as missing: {nonfinite_counts['BMdec']:,}")
    print(f"Non-finite GP values treated as missing: {nonfinite_counts['GP']:,}")
    for method in ["VW", "EW"]:
        for result in results[method]:
            print(
                f"{method} spec {result['specification']}: "
                f"N={result['nobs']}, months={result['months']}, "
                f"L={result['bandwidth']}, raw L={result['raw_bandwidth']:.4f}"
            )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
