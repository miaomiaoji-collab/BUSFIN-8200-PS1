"""Calculate the infinite-horizon VAR decomposition for Question 1d.

The script uses the same annual-transition VAR and fixed 1,117-observation
estimation sample as Question 1c. It evaluates the discounted matrix geometric
series directly and writes the two infinite-horizon slopes and their sum.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from q1_c import STATE_COLUMNS, covariance_slope, estimate_var, validate_data


DEFAULT_INPUT = Path("data/EQ Dataset.csv")
DEFAULT_OUTPUT = Path("output/q1d_infinite_horizon.csv")
EXPECTED_VAR_OBSERVATIONS = 1_117


def calculate_infinite_horizon_decomposition(
    data: pd.DataFrame,
) -> tuple[float, float, float, float, float, float]:
    """Return kappa, convergence diagnostics, and the Q1d coefficients."""
    validated = validate_data(data)
    kappa = float(1.0 / (1.0 + np.exp(validated["dp"].mean())))
    _, gamma, current_states = estimate_var(validated)

    if len(current_states) != EXPECTED_VAR_OBSERVATIONS:
        raise ValueError(
            "Question 1d requires the same 1,117-observation VAR sample as Q1c; "
            f"found {len(current_states):,}."
        )

    scaled_spectral_radius = float(
        np.max(np.abs(np.linalg.eigvals(kappa * gamma)))
    )
    if scaled_spectral_radius >= 1.0:
        raise ValueError(
            "The infinite discounted VAR series does not converge because the "
            "spectral radius of kappa*Gamma is not below one."
        )

    system = np.eye(gamma.shape[0]) - kappa * gamma
    condition_number = float(np.linalg.cond(system))
    if not np.isfinite(condition_number) or condition_number > 1.0e12:
        raise ValueError("I - kappa*Gamma is singular or numerically ill-conditioned.")

    # Solve M = Gamma @ inv(I - kappa*Gamma) without forming the inverse.
    geometric_sum = np.linalg.solve(system.T, gamma.T).T

    dg_index = STATE_COLUMNS.index("dg")
    re_index = STATE_COLUMNS.index("re")
    dp_index = STATE_COLUMNS.index("dp")
    current_dp = current_states[:, dp_index]

    expected_return_component = current_states @ geometric_sum[re_index]
    expected_dividend_growth_component = -(
        current_states @ geometric_sum[dg_index]
    )

    b_re_infinity = covariance_slope(current_dp, expected_return_component)
    b_dg_infinity = covariance_slope(
        current_dp, expected_dividend_growth_component
    )
    sum_b_infinity = b_re_infinity + b_dg_infinity

    return (
        kappa,
        scaled_spectral_radius,
        condition_number,
        b_re_infinity,
        b_dg_infinity,
        sum_b_infinity,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate the Question 1d infinite-horizon VAR decomposition."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = pd.read_csv(args.input)
    (
        kappa,
        scaled_spectral_radius,
        condition_number,
        b_re_infinity,
        b_dg_infinity,
        sum_b_infinity,
    ) = calculate_infinite_horizon_decomposition(data)

    results = pd.DataFrame(
        [
            {
                "b_re_infinity": b_re_infinity,
                "b_dg_infinity": b_dg_infinity,
                "sum_b_infinity": sum_b_infinity,
            }
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output, index=False, float_format="%.12f")

    print(f"b_re(infinity) = {b_re_infinity:.12f}")
    print(f"b_dg(infinity) = {b_dg_infinity:.12f}")
    print(f"sum_b_infinity = {sum_b_infinity:.12f}")
    print(f"kappa = {kappa:.12f}")
    print(f"spectral radius of kappa*Gamma = {scaled_spectral_radius:.12f}")
    print(f"condition number of I - kappa*Gamma = {condition_number:.12f}")
    print(f"Saved infinite-horizon values to {args.output}")


if __name__ == "__main__":
    main()
