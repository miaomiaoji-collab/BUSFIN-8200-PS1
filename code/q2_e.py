"""Implement the restricted expanding-window forecasts for Question 2e."""

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

from q2_d import (
    MONTHS_PER_YEAR,
    OOS_START_DATE,
    ROLLING_START_DATE,
    ROLLING_WINDOW_MONTHS,
    create_rolling_figure,
    fit_ols,
    validate_data,
)


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_FORECAST_FIGURE = Path("output/q2e_forecasts.pdf")
DEFAULT_ROLLING_FIGURE = Path("output/q2e_rolling_r2os.pdf")
DEFAULT_RESULTS_OUTPUT = Path("output/q2e_results.csv")


def prepare_restricted_pairs(data: pd.DataFrame) -> pd.DataFrame:
    """Align predictors, annual outcomes, and annual dividend growth."""
    if "dg" not in data.columns:
        raise ValueError("EQ Dataset is missing required column: dg")
    prepared = data.copy()
    prepared["dg"] = pd.to_numeric(prepared["dg"], errors="raise")
    if prepared["dg"].isna().any():
        raise ValueError("The dg column contains missing values.")

    validated = validate_data(prepared)
    validated["dividend_growth_level"] = np.exp(validated["dg"])
    if not np.isfinite(validated["dividend_growth_level"]).all():
        raise ValueError("Constructed exp(dg) contains non-finite values.")

    pairs = pd.DataFrame(
        {
            "predictor_date": validated["date"],
            "forecast_date": validated["date"].shift(-MONTHS_PER_YEAR),
            "predictor": validated["dividend_price"],
            "realized_excess_return": validated["excess_return"].shift(
                -MONTHS_PER_YEAR
            ),
            "realized_dividend_growth": validated[
                "dividend_growth_level"
            ].shift(-MONTHS_PER_YEAR),
        }
    ).dropna()
    expected_nobs = len(validated) - MONTHS_PER_YEAR
    if len(pairs) != expected_nobs:
        raise RuntimeError(
            f"Predictive sample should contain {expected_nobs} observations; "
            f"found {len(pairs)}."
        )
    return pairs.reset_index(drop=True)


def calculate_rolling_r2(forecasts: pd.DataFrame) -> pd.DataFrame:
    """Calculate 50-year rolling OOS R-squared from forecast errors."""
    rows: list[dict[str, float | pd.Timestamp]] = []
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
        rows.append(
            {
                "forecast_date": observation.forecast_date,
                "rolling_r2_os": 1.0
                - float(window["model_squared_error"].sum()) / denominator,
            }
        )

    rolling = pd.DataFrame(rows)
    if rolling.empty or rolling["forecast_date"].iloc[0] != ROLLING_START_DATE:
        raise RuntimeError("The rolling R-squared series must begin in December 1990.")
    return rolling


def estimate_restricted_forecasts(
    data: pd.DataFrame,
) -> tuple[pd.DataFrame, float, pd.DataFrame, np.ndarray]:
    """Estimate the restricted forecasts and overall and rolling OOS R-squared."""
    pairs = prepare_restricted_pairs(data)
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
            raise ValueError("An expanding-window sample has too few observations.")

        historical_mean = float(training["realized_excess_return"].mean())
        g_t = float(training["realized_dividend_growth"].mean())
        a_t = g_t - 1.0
        b_t = g_t
        restricted_forecast = a_t + b_t * observation.predictor

        rows.append(
            {
                "forecast_date": observation.forecast_date,
                "predictor_date": observation.predictor_date,
                "realized_excess_return": observation.realized_excess_return,
                "historical_mean": historical_mean,
                "in_sample_fitted": full_coefficients[0]
                + full_coefficients[1] * observation.predictor,
                "restricted_oos_forecast": restricted_forecast,
                "G_t": g_t,
                "a_t": a_t,
                "b_t": b_t,
                "training_nobs": len(training),
            }
        )

    forecasts = pd.DataFrame(rows)
    forecasts["model_squared_error"] = (
        forecasts["realized_excess_return"]
        - forecasts["restricted_oos_forecast"]
    ) ** 2
    forecasts["mean_squared_error"] = (
        forecasts["realized_excess_return"] - forecasts["historical_mean"]
    ) ** 2

    denominator = float(forecasts["mean_squared_error"].sum())
    if denominator <= 0.0:
        raise ValueError("Historical-mean forecast errors have no variation.")
    overall_r2_os = 1.0 - float(forecasts["model_squared_error"].sum()) / denominator
    rolling = calculate_rolling_r2(forecasts)
    return forecasts, overall_r2_os, rolling, full_coefficients


def create_forecast_figure(forecasts: pd.DataFrame, output_path: Path) -> None:
    """Plot the benchmark, in-sample fit, and restricted OOS forecast."""
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
        forecasts["restricted_oos_forecast"],
        color="#b2182b",
        linewidth=1.2,
        label=r"Restricted OOS forecast, $\widehat{E}^{OS}_t[xRe]$",
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


def summarize_results(
    forecasts: pd.DataFrame,
    overall_r2_os: float,
    rolling: pd.DataFrame,
) -> pd.DataFrame:
    """Create the requested CSV summary of the OOS evaluation."""
    return pd.DataFrame(
        [
            {
                "overall_r2_os": overall_r2_os,
                "oos_start_date": forecasts["forecast_date"].iloc[0].strftime(
                    "%Y-%m"
                ),
                "oos_end_date": forecasts["forecast_date"].iloc[-1].strftime(
                    "%Y-%m"
                ),
                "n_forecasts": len(forecasts),
                "rolling_start_date": rolling["forecast_date"].iloc[0].strftime(
                    "%Y-%m"
                ),
                "rolling_end_date": rolling["forecast_date"].iloc[-1].strftime(
                    "%Y-%m"
                ),
                "rolling_window_months": ROLLING_WINDOW_MONTHS,
                "initial_G_t": forecasts["G_t"].iloc[0],
                "final_G_t": forecasts["G_t"].iloc[-1],
            }
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Implement Question 2e.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--forecast-figure", type=Path, default=DEFAULT_FORECAST_FIGURE
    )
    parser.add_argument(
        "--rolling-figure", type=Path, default=DEFAULT_ROLLING_FIGURE
    )
    parser.add_argument("--results-output", type=Path, default=DEFAULT_RESULTS_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = pd.read_csv(args.input)
    forecasts, overall_r2_os, rolling, full_coefficients = (
        estimate_restricted_forecasts(data)
    )
    create_forecast_figure(forecasts, args.forecast_figure)
    create_rolling_figure(rolling, args.rolling_figure)
    results = summarize_results(forecasts, overall_r2_os, rolling)
    args.results_output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.results_output, index=False, float_format="%.12f")

    print(f"Full-sample intercept = {full_coefficients[0]:.12f}")
    print(f"Full-sample slope = {full_coefficients[1]:.12f}")
    print(f"Out-of-sample forecasts = {len(forecasts)}")
    print(
        "Out-of-sample period = "
        f"{forecasts['forecast_date'].iloc[0]:%Y-%m} to "
        f"{forecasts['forecast_date'].iloc[-1]:%Y-%m}"
    )
    print(f"Initial G_t = {forecasts['G_t'].iloc[0]:.12f}")
    print(f"Final G_t = {forecasts['G_t'].iloc[-1]:.12f}")
    print(f"Overall R2_OS = {overall_r2_os:.12f}")
    print(
        "Rolling R2_OS period = "
        f"{rolling['forecast_date'].iloc[0]:%Y-%m} to "
        f"{rolling['forecast_date'].iloc[-1]:%Y-%m}"
    )
    print(f"Saved forecast figure to {args.forecast_figure}")
    print(f"Saved rolling R2_OS figure to {args.rolling_figure}")
    print(f"Saved result summary to {args.results_output}")


if __name__ == "__main__":
    main()
