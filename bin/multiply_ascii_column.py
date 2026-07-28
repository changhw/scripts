#!/usr/bin/env python3
"""Multiply one column of a whitespace-delimited ASCII file by a factor."""

from __future__ import annotations

import argparse
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path


FIELD_RE = re.compile(r"\S+")


def decimal_value(text: str) -> Decimal:
    """Parse ordinary and Fortran-style floating-point values."""
    return Decimal(text.replace("D", "E").replace("d", "e"))


def output_path(input_path: Path, factor_label: str) -> Path:
    """Add x<factor> to the filename immediately before its extension."""
    return input_path.with_name(
        f"{input_path.stem}_x{factor_label}{input_path.suffix}"
    )


def multiply_column(
    input_path: Path, column: int, factor: Decimal, factor_label: str
) -> Path:
    destination = output_path(input_path, factor_label)

    with input_path.open("r", encoding="ascii") as source, destination.open(
        "w", encoding="ascii"
    ) as target:
        for line_number, line in enumerate(source, start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                target.write(line)
                continue

            fields = list(FIELD_RE.finditer(line))
            if column >= len(fields):
                raise ValueError(
                    f"line {line_number} has {len(fields)} columns; "
                    f"column {column} was requested"
                )

            field = fields[column]
            original = field.group()
            try:
                multiplied = decimal_value(original) * factor
            except InvalidOperation as exc:
                raise ValueError(
                    f"line {line_number}, column {column} is not numeric: "
                    f"{original!r}"
                ) from exc

            target.write(
                line[: field.start()] + str(multiplied) + line[field.end() :]
            )

    return destination


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Multiply a zero-based column in a whitespace-delimited ASCII file. "
            "The output filename gets the suffix xFACTOR before its extension."
        )
    )
    parser.add_argument("input_file", type=Path, help="input ASCII file")
    parser.add_argument("column", type=int, help="zero-based column rank")
    parser.add_argument("factor", help="multiplication factor, e.g. 3.0")
    args = parser.parse_args()

    if args.column < 0:
        parser.error("column must be zero or greater")
    if Path(args.factor).name != args.factor:
        parser.error("factor cannot contain a path separator")
    try:
        args.factor_value = decimal_value(args.factor)
    except InvalidOperation:
        parser.error(f"factor must be numeric: {args.factor!r}")

    return args


def main() -> None:
    args = parse_arguments()
    try:
        destination = multiply_column(
            args.input_file, args.column, args.factor_value, args.factor
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    print(destination)


if __name__ == "__main__":
    main()
