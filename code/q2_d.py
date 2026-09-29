"""Implement the expanding-window forecasts required for Question 2d."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_FORECAST_FIGURE = Path("output/q2d_forecasts.pdf")
DEFAULT_ROLLING_FIGURE = Path("output/q2d_rolling_r2os.pdf")
DEFAULT_TABLE_OUTPUT = Path("output/q2d_results.tex")
REQUIRED_COLUMNS = {"YEAR", "MONTH", "dp", "rf", "re"}
MONTHS_PER_YEAR = 12
OOS_START_DATE = pd.Timestamp("1940-12-01")
ROLLING_START_DATE = pd.Timestamp("1990-12-01")
ROLLING_WINDOW_MONTHS = 50 * MONTHS_PER_YEAR


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


def fit_ols(y: np.ndarray, predictor: np.ndarray) -> np.ndarray:
    """Estimate an intercept and slope by OLS."""
    y = np.asarray(y, dtype="float64")
    predictor = np.asarray(predictor, dtype="float64")
    design = np.column_stack([np.ones(len(y)), predictor])
    coefficients, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
    if rank != design.shape[1]:
        raise ValueError("Regression design matrix is not full column rank.")
    return coefficients


def prepare_predictive_pairs(data: pd.DataFrame) -> pd.DataFrame:
    """Align each predictor month with the annual return ending 12 months later."""
    validated = validate_data(data)
    pairs = pd.DataFrame(
        {
            "predictor_date": validated["date"],
            "forecast_date": validated["date"].shift(-MONTHS_PER_YEAR),
            "predictor": validated["dividend_price"],
            "realized_excess_return": validated["excess_return"].shift(
                -MONTHS_PER_YEAR
            ),
        }
    ).dropna()
    expected_nobs = len(validated) - MONTHS_PER_YEAR
    if len(pairs) != expected_nobs:
        raise RuntimeError(
            f"Predictive sample should contain {expected_nobs} observations; "
            f"found {len(pairs)}."
        )
    return pairs.reset_index(drop=True)


def estimate_oos_forecasts(
    data: pd.DataFrame,
) -> tuple[pd.DataFrame, float, pd.DataFrame, np.ndarray]:
    """Estimate expanding-window forecasts and overall and rolling OOS R-squared."""
    pairs = prepare_predictive_pairs(data)
    full_coefficients = fit_ols(
        pairs["realized_excess_return"].to_numpy(),
        pairs["predictor"].to_numpy(),
    )
    oos_pairs = pairs.loc[pairs["forecast_date"] >= OOS_START_DATE].copy()
    if oos_pairs.empty or oos_pairs["forecast_date"].iloc[0] != OOS_START_DATE:
        raise ValueError("The dataset does not contain the December 1940 forecast date.")

    rows: list[dict[str, float | int | pd.Timestamp]] = []
    for observation in oos_pairs.itertuples(index=False):
        training = pairs.loc[pairs["forecast_date"] <= observation.predictor_date]
        if len(training) <= 2:
            raise ValueError("An expanding-window regression has too few observations.")
        coefficients = fit_ols(
            training["realized_excess_return"].to_numpy(),
            training["predictor"].to_numpy(),
        )
        rows.append(
            {
                "forecast_date": observation.forecast_date,
                "predictor_date": observation.predictor_date,
                "realized_excess_return": observation.realized_excess_return,
                "historical_mean": training["realized_excess_return"].mean(),
                "in_sample_fitted": full_coefficients[0]
                + full_coefficients[1] * observation.predictor,
                "oos_forecast": coefficients[0]
                + coefficients[1] * observation.predictor,
                "a_t": coefficients[0],
                "b_t": coefficients[1],
                "training_nobs": len(training),
            }
        )
    forecasts = pd.DataFrame(rows)
    forecasts["model_squared_error"] = (
        forecasts["realized_excess_return"] - forecasts["oos_forecast"]
    ) ** 2
    forecasts["mean_squared_error"] = (
        forecasts["realized_excess_return"] - forecasts["historical_mean"]
    ) ** 2

    benchmark_sse = float(forecasts["mean_squared_error"].sum())
    if benchmark_sse <= 0.0:
        raise ValueError("Historical-mean forecast errors have no variation.")
    overall_r2_os = 1.0 - float(forecasts["model_squared_error"].sum()) / benchmark_sse

    rolling_rows: list[dict[str, float | pd.Timestamp]] = []
    for position, observation in enumerate(forecasts.itertuples(index=False)):
        if observation.forecast_date < ROLLING_START_DATE:
            continue
        window_start = position - ROLLING_WINDOW_MONTHS + 1
        if window_start < 0:
            raise RuntimeError("The requested rolling window is not yet available.")
        window = forecasts.iloc[window_start : position + 1]
        denominator = float(window["mean_squared_error"].sum())
        if denominator <= 0.0:
            raise ValueError("A rolling historical-mean error sum is not positive.")
        rolling_rows.append(
            {
                "forecast_date": observation.forecast_date,
                "rolling_r2_os": 1.0
                - float(window["model_squared_error"].sum()) / denominator,
            }
        )
    rolling = pd.DataFrame(rolling_rows)
    if rolling.empty or rolling["forecast_date"].iloc[0] != ROLLING_START_DATE:
        raise RuntimeError("The rolling R-squared series must begin in December 1990.")
    return forecasts, overall_r2_os, rolling, full_coefficients


def create_forecast_figure(forecasts: pd.DataFrame, output_path: Path) -> None:
    """Plot the historical mean, full-sample fitted values, and OOS forecasts."""
    figure, axis = plt.subplots(figsize=(10.5, 5.3))
    axis.plot(
        forecasts["forecast_date"],
        forecasts["historical_mean"],
        color="#4d4d4d",
        linewidth=1.4,
        label=r"Historical mean, $\overline{xRe}_t$",
    )
    axis.plot(
        forecasts["forecast_date"],
        forecasts["in_sample_fitted"],
        color="#2166ac",
        linewidth=1.2,
        label=r"In-sample fitted value, $\widehat{E}^{IS}_t[xRe]$",
    )
    axis.plot(
        forecasts["forecast_date"],
        forecasts["oos_forecast"],
        color="#b2182b",
        linewidth=1.2,
        label=r"Out-of-sample forecast, $\widehat{E}^{OS}_t[xRe]$",
    )
    axis.set_xlabel("Forecast date")
    axis.set_ylabel("Expected annual excess return")
    axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    axis.xaxis.set_major_locator(mdates.YearLocator(10))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7)
    axis.legend(frameon=False, ncol=1, loc="best")
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def create_rolling_figure(rolling: pd.DataFrame, output_path: Path) -> None:
    """Plot the 50-year rolling out-of-sample R-squared series."""
    figure, axis = plt.subplots(figsize=(9.0, 4.8))
    axis.plot(
        rolling["forecast_date"],
        rolling["rolling_r2_os"],
        color="#2166ac",
        linewidth=1.6,
    )
    axis.axhline(0.0, color="#4d4d4d", linewidth=0.8)
    axis.set_xlabel("Forecast date")
    axis.set_ylabel(r"50-year rolling $R^2_{OS}$")
    axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    axis.xaxis.set_major_locator(mdates.YearLocator(5))
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axis.grid(axis="y", color="#d9d9d9", linewidth=0.7)
    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def format_latex_table(forecasts: pd.DataFrame, overall_r2_os: float) -> str:
    """Format the requested overall OOS R-squared summary table."""
    start_date = forecasts["forecast_date"].iloc[0].strftime("%B %Y")
    end_date = forecasts["forecast_date"].iloc[-1].strftime("%B %Y")
    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Out-of-sample equity-return predictability}",
            r"\label{tab:q2d_results}",
            r"\begin{tabular}{lccr}",
            r"\toprule",
            r"Overall $R^2_{OS}$ & Start date & End date & Forecasts \\",
            r"\midrule",
            (
                f"{overall_r2_os:.4f} & {start_date} & {end_date} & "
                f"{len(forecasts)} \\\\"
            ),
            r"\bottomrule",
            r"\end{tabular}",
            r"\begin{minipage}{0.92\textwidth}",
            r"\footnotesize\textit{Note:} Forecasts use an expanding-window predictive regression. The benchmark is the expanding-window historical mean computed over the same estimation sample.",
            r"\end{minipage}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 2d.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--forecast-figure", type=Path, default=DEFAULT_FORECAST_FIGURE
    )
    parser.add_argument(
        "--rolling-figure", type=Path, default=DEFAULT_ROLLING_FIGURE
    )
    parser.add_argument("--table-output", type=Path, default=DEFAULT_TABLE_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = pd.read_csv(args.input)
    forecasts, overall_r2_os, rolling, full_coefficients = estimate_oos_forecasts(
        data
    )
    create_forecast_figure(forecasts, args.forecast_figure)
    create_rolling_figure(rolling, args.rolling_figure)
    args.table_output.parent.mkdir(parents=True, exist_ok=True)
    args.table_output.write_text(
        format_latex_table(forecasts, overall_r2_os), encoding="utf-8"
    )

    print(f"Full-sample intercept = {full_coefficients[0]:.12f}")
    print(f"Full-sample slope = {full_coefficients[1]:.12f}")
    print(f"Out-of-sample forecasts = {len(forecasts)}")
    print(
        "Out-of-sample period = "
        f"{forecasts['forecast_date'].iloc[0]:%Y-%m} to "
        f"{forecasts['forecast_date'].iloc[-1]:%Y-%m}"
    )
    print(f"Overall R2_OS = {overall_r2_os:.12f}")
    print(
        "Rolling R2_OS period = "
        f"{rolling['forecast_date'].iloc[0]:%Y-%m} to "
        f"{rolling['forecast_date'].iloc[-1]:%Y-%m}"
    )
    print(f"Saved forecast figure to {args.forecast_figure}")
    print(f"Saved rolling R2_OS figure to {args.rolling_figure}")
    print(f"Saved results table to {args.table_output}")


if __name__ == "__main__":
    main()
