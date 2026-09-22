"""Prepare the CRSP Fama-Bliss bond-yield data for Question 4.

This script implements only the data-preparation rules in ``spec/q4.md``.
It does not construct yields, forward rates, returns, tables, or regressions.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_INPUT = Path("data/Bond Dataset.csv")
DEFAULT_OUTPUT = Path("data/q4_bond_data_prepared.csv")
REQUIRED_COLUMNS = {"MCALDT", "TTERMLBL", "TMYTM"}
MATURITY_PATTERN = r"^Fama Bliss Discount Bonds - (?P<H>[1-5])-Year \(Nominal\)$"
EXPECTED_MATURITIES = {1, 2, 3, 4, 5}


def prepare_bond_data(input_path: Path) -> pd.DataFrame:
    """Read and prepare the five monthly Fama-Bliss discount-bond series."""
    raw = pd.read_csv(input_path)

    missing_columns = REQUIRED_COLUMNS.difference(raw.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Input data are missing required columns: {missing}")

    maturity = raw["TTERMLBL"].astype("string").str.extract(
        MATURITY_PATTERN, expand=True
    )["H"]
    prepared = raw.loc[maturity.notna()].copy()

    if prepared.empty:
        raise ValueError("No 1- to 5-year nominal Fama-Bliss discount bonds found.")

    prepared["MCALDT"] = pd.to_datetime(
        prepared["MCALDT"], format="%Y-%m-%d", errors="raise"
    )
    prepared["month"] = prepared["MCALDT"].dt.strftime("%Y-%m")
    prepared["H"] = maturity.loc[prepared.index].astype("int64")
    prepared["TMYTM"] = pd.to_numeric(prepared["TMYTM"], errors="raise")

    if prepared[["MCALDT", "TTERMLBL", "TMYTM"]].isna().any().any():
        raise ValueError(
            "Relevant rows contain missing dates, maturity labels, or quoted yields; "
            "the specification does not define a missing-data treatment."
        )

    observed_maturities = set(prepared["H"].unique())
    if observed_maturities != EXPECTED_MATURITIES:
        raise ValueError(
            "Expected maturities H=1,...,5, but found "
            f"{sorted(observed_maturities)}."
        )

    duplicate = prepared.duplicated(subset=["month", "H"], keep=False)
    if duplicate.any():
        examples = prepared.loc[duplicate, ["month", "H"]].head().to_dict("records")
        raise ValueError(f"Duplicate month-maturity observations found: {examples}")

    maturities_per_month = prepared.groupby("month", sort=False)["H"].nunique()
    incomplete_months = maturities_per_month[maturities_per_month != 5]
    if not incomplete_months.empty:
        examples = incomplete_months.head().to_dict()
        raise ValueError(
            "Some months do not contain all five maturities; the specification does "
            f"not define a treatment. Examples: {examples}"
        )

    prepared["Y"] = prepared["TMYTM"] / 100.0
    prepared = prepared.sort_values(["MCALDT", "H"], kind="stable").reset_index(
        drop=True
    )
    return prepared


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare the Question 4 Fama-Bliss bond-yield dataset."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Raw CRSP CSV file (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Prepared CSV file (default: {DEFAULT_OUTPUT}).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prepared = prepare_bond_data(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prepared.to_csv(args.output, index=False, date_format="%Y-%m-%d")
    print(
        f"Saved {len(prepared):,} observations "
        f"({prepared['month'].nunique():,} months, H=1,...,5) to {args.output}"
    )


if __name__ == "__main__":
    main()
