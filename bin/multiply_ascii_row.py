#!/usr/bin/env python3
import argparse
import re



import argparse
import re


def scale_row(input_file, output_file, row_number, factor):
    """
    Multiply all numeric values in a specified row by a factor.

    Row numbering starts from 0.
    Supports comma, space, tab, and mixed delimiters.
    Preserves all other rows and original delimiters.
    """

    with open(input_file, "r", encoding="ascii", newline="") as f:
        lines = f.readlines()

    # Zero-based row indexing
    if not 0 <= row_number < len(lines):
        raise ValueError(
            f"Row {row_number} is out of range (0-{len(lines)-1})"
        )

    line = lines[row_number]
    content = line.rstrip("\r\n")
    newline = line[len(content):]

    # Split while preserving commas, spaces, and tabs
    parts = re.split(r"([,\t ]+)", content)

    for i, part in enumerate(parts):
        if not part or re.fullmatch(r"[,\t ]+", part):
            continue

        # Support standard and Fortran D-exponent notation
        value = float(part.replace("D", "E").replace("d", "e"))
        parts[i] = f"{value * factor:.15g}"

    lines[row_number] = "".join(parts) + newline

    with open(output_file, "w", encoding="ascii", newline="") as f:
        f.writelines(lines)

    print(
        f"Row {row_number} multiplied by {factor}. "
        f"Output saved to: {output_file}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Multiply a specific row (zero-based) of an ASCII file by a factor."
    )
    parser.add_argument("input_file", help="Input ASCII file")
    parser.add_argument("output_file", help="Output ASCII file")
    parser.add_argument(
        "row", type=int, help="Zero-based row number"
    )
    parser.add_argument(
        "factor", type=float, help="Multiplication factor"
    )

    args = parser.parse_args()

    scale_row(
        args.input_file,
        args.output_file,
        args.row,
        args.factor
    )



