"""Construct the bond variables and summary table for Question 4a.

The script reads the prepared long-form bond data created by
``code/q4_clean_bond_data.py``. It preserves one row per month and maturity,
adds the variables specified in ``spec/q4.md``, and writes both the enriched
data and the LaTeX table requested in the specification.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_INPUT = Path("data/q4_bond_data_prepared.csv")
DEFAULT_DATA_OUTPUT = Path("output/q4_bond_variables.csv")
DEFAULT_TABLE_OUTPUT = Path("output/q4a_table.tex")
EXPECTED_MATURITIES = [1, 2, 3, 4, 5]
REQUIRED_COLUMNS = {"month", "H", "Y"}


def validate_prepared_data(data: pd.DataFrame) -> pd.DataFrame:
    """Validate and sort the prepared monthly panel used by Question 4a."""
    missing_columns = REQUIRED_COLUMNS.difference(data.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Prepared data are missing required columns: {missing}")

    validated = data.copy()
    validated["month"] = pd.to_datetime(
        validated["month"], format="%Y-%m", errors="raise"
    ).dt.strftime("%Y-%m")
    validated["H"] = pd.to_numeric(validated["H"], errors="raise")
    validated["Y"] = pd.to_numeric(validated["Y"], errors="raise")

    if validated[["month", "H", "Y"]].isna().any().any():
        raise ValueError("Prepared data contain missing month, H, or Y values.")
    if not np.equal(validated["H"], np.floor(validated["H"])).all():
        raise ValueError("Maturity H must be integer-valued.")
    validated["H"] = validated["H"].astype("int64")

    observed_maturities = sorted(validated["H"].unique().tolist())
    if observed_maturities != EXPECTED_MATURITIES:
        raise ValueError(
            f"Expected maturities {EXPECTED_MATURITIES}, found {observed_maturities}."
        )
    if (validated["Y"] <= -1).any():
        raise ValueError("All Y values must exceed -1 so log(1 + Y) is defined.")

    duplicate = validated.duplicated(subset=["month", "H"], keep=False)
    if duplicate.any():
        examples = validated.loc[duplicate, ["month", "H"]].head().to_dict("records")
        raise ValueError(f"Duplicate month-maturity observations found: {examples}")

    counts = validated.groupby("month", sort=False)["H"].nunique()
    incomplete = counts[counts != len(EXPECTED_MATURITIES)]
    if not incomplete.empty:
        raise ValueError(
            "Every month must contain H=1,...,5. Incomplete examples: "
            f"{incomplete.head().to_dict()}"
        )

    return validated.sort_values(["month", "H"], kind="stable").reset_index(drop=True)


def construct_bond_variables(data: pd.DataFrame) -> pd.DataFrame:
    """Add y, f, r, xy, xf, and xr exactly as specified for Question 4a."""
    result = validate_prepared_data(data)

    yields = result.pivot(index="month", columns="H", values="Y").reindex(
        columns=EXPECTED_MATURITIES
    )
    log_yields = np.log1p(yields)

    forwards = pd.DataFrame(index=log_yields.index, columns=EXPECTED_MATURITIES)
    forwards[1] = log_yields[1]
    for maturity in EXPECTED_MATURITIES[1:]:
        forwards[maturity] = (
            maturity * log_yields[maturity]
            - (maturity - 1) * log_yields[maturity - 1]
        )

    returns = pd.DataFrame(index=log_yields.index, columns=EXPECTED_MATURITIES)
    returns[1] = log_yields[1].shift(1)
    for maturity in EXPECTED_MATURITIES[1:]:
        returns[maturity] = (
            maturity * log_yields[maturity].shift(1)
            - (maturity - 1) * log_yields[maturity - 1]
        )

    yield_spreads = log_yields.subtract(log_yields[1], axis="index")
    forward_spreads = forwards.subtract(forwards[1], axis="index")
    return_spreads = returns.subtract(returns[1], axis="index")

    # The spread variables are defined only for H=2,...,5 in the specification.
    for spread in (yield_spreads, forward_spreads, return_spreads):
        spread[1] = np.nan

    matrices = {
        "y": log_yields,
        "f": forwards,
        "r": returns,
        "xy": yield_spreads,
        "xf": forward_spreads,
        "xr": return_spreads,
    }
    long_variables: pd.DataFrame | None = None
    for variable, matrix in matrices.items():
        long_matrix = matrix.rename_axis(columns="H").reset_index().melt(
            id_vars="month", var_name="H", value_name=variable
        )
        if long_variables is None:
            long_variables = long_matrix
        else:
            long_variables = long_variables.merge(
                long_matrix, on=["month", "H"], how="inner", validate="one_to_one"
            )

    if long_variables is None:  # pragma: no cover - matrices is fixed and non-empty
        raise RuntimeError("No variables were constructed.")

    result = result.merge(
        long_variables, on=["month", "H"], how="left", validate="one_to_one"
    )
    return result


def calculate_sample_means(data: pd.DataFrame) -> pd.DataFrame:
    """Return sample means of xy, xf, and xr for H=2,3,4,5."""
    return (
        data.loc[data["H"].isin(EXPECTED_MATURITIES[1:])]
        .groupby("H", sort=True)[["xy", "xf", "xr"]]
        .mean()
        .reindex(EXPECTED_MATURITIES[1:])
        .T
    )


def format_latex_table(means: pd.DataFrame, decimals: int = 6) -> str:
    """Create a compact LaTeX table of the Question 4a sample means."""
    column_labels = " & ".join(f"$H={maturity}$" for maturity in means.columns)
    row_labels = {"xy": r"Mean $xy(H)$", "xf": r"Mean $xf(H)$", "xr": r"Mean $xr(H)$"}
    rows = []
    for variable in ("xy", "xf", "xr"):
        values = " & ".join(
            f"{value:.{decimals}f}" for value in means.loc[variable]
        )
        rows.append(f"{row_labels[variable]} & {values} \\\\")

    return "\n".join(
        [
            r"\begin{table}[htbp]",
            r"\centering",
            r"\caption{Average yield, forward-rate, and bond-return spreads}",
            r"\label{tab:q4a-averages}",
            r"\begin{tabular}{lrrrr}",
            r"\toprule",
            f" & {column_labels} \\\\",
            r"\midrule",
            *rows,
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construct Question 4a bond variables.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT)
    parser.add_argument("--table-output", type=Path, default=DEFAULT_TABLE_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prepared = pd.read_csv(args.input)
    variables = construct_bond_variables(prepared)
    means = calculate_sample_means(variables)

    args.data_output.parent.mkdir(parents=True, exist_ok=True)
    args.table_output.parent.mkdir(parents=True, exist_ok=True)
    variables.to_csv(args.data_output, index=False, date_format="%Y-%m-%d")
    args.table_output.write_text(format_latex_table(means), encoding="utf-8")

    print(
        f"Saved {len(variables):,} observations to {args.data_output} "
        f"and the sample-mean table to {args.table_output}."
    )


if __name__ == "__main__":
    main()
