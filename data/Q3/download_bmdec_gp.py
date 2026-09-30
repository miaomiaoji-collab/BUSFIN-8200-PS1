#!/usr/bin/env python3
"""Download firm-month BMdec and GP data from Open Asset Pricing."""

from pathlib import Path
import argparse

import openassetpricing as oap
import polars as pl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release",
        type=int,
        default=202510,
        help="Open Asset Pricing release (default: 202510)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/Q3/bmdec_gp_firm_monthly_202510.csv"),
        help="Destination CSV path",
    )
    args = parser.parse_args()

    data = oap.OpenAP(args.release).dl_signal(
        "polars",
        ["BMdec", "GP"],
    )

    if data is None or data.is_empty():
        raise RuntimeError("The downloader returned no observations.")

    expected = {"permno", "yyyymm", "BMdec", "GP"}
    if set(data.columns) != expected:
        raise RuntimeError(f"Unexpected columns: {data.columns}")

    if data.select(
        pl.struct("permno", "yyyymm").is_duplicated().sum()
    ).item():
        raise RuntimeError("Duplicate permno-yyyymm rows found.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    data.write_csv(args.output)

    print(f"Saved {data.height:,} rows to {args.output}")


if __name__ == "__main__":
    main()