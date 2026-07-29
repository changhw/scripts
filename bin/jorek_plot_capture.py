#!/usr/bin/env python3
"""Run interactive JOREK plotting utilities and capture their figures as PNG."""

import glob
import os
import pickle
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path


def parse_command_line():
    try:
        separator = sys.argv.index("--")
    except ValueError:
        raise SystemExit("usage: jorek_plot_capture.py --output-dir DIR --mode MODE -- SCRIPT [ARGS]")
    options, command = sys.argv[1:separator], sys.argv[separator + 1:]
    output_directory = None
    mode = "python"
    index = 0
    while index < len(options):
        if options[index] == "--output-dir" and index + 1 < len(options):
            output_directory = Path(options[index + 1])
            index += 2
        elif options[index] == "--mode" and index + 1 < len(options):
            mode = options[index + 1]
            index += 2
        else:
            raise SystemExit("unknown capture option: {}".format(options[index]))
    if output_directory is None or not command:
        raise SystemExit("an output directory and script are required")
    output_directory.mkdir(parents=True, exist_ok=True)
    return output_directory, mode, command


def expand_globs(arguments):
    expanded = []
    for argument in arguments:
        matches = sorted(glob.glob(argument)) if any(char in argument for char in "*?[") else []
        expanded.extend(matches or [argument])
    return expanded


def save_matplotlib_figures(output_directory):
    import matplotlib.pyplot as plt

    paths = []
    for index, figure_number in enumerate(plt.get_fignums(), 1):
        figure = plt.figure(figure_number)
        try:
            figure.tight_layout()
        except Exception:
            pass
        path = output_directory / "figure-{:03d}.png".format(index)
        figure.savefig(str(path), dpi=130, bbox_inches="tight")
        paths.append(path)
        print("Captured {}".format(path), flush=True)
        object_path = output_directory / "figure-{:03d}.mplfig".format(index)
        try:
            with object_path.open("wb") as output:
                pickle.dump(figure, output, protocol=pickle.HIGHEST_PROTOCOL)
            print("Saved interactive figure {}".format(object_path), flush=True)
        except Exception as exc:
            if object_path.exists():
                object_path.unlink()
            print(
                "Interactive figure unavailable; PNG fallback will be used: {}".format(exc),
                flush=True,
            )
    return paths


def run_python(output_directory, command):
    os.environ["MPLBACKEND"] = "Agg"
    sys.dont_write_bytecode = True
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    script = Path(command[0]).resolve()
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)] + expand_globs(command[1:])
    plt.show = lambda *args, **kwargs: None
    original_parse_args = None
    if script.name == "plot_f_versus_time.py":
        # The utility declares --skiprows without type=int, but later performs
        # numeric comparisons on it.  Normalize the parsed namespace here so
        # CLI values work just like the integer default.
        import argparse
        original_parse_args = argparse.ArgumentParser.parse_args

        def parse_args_with_integer_skiprows(parser, *args, **kwargs):
            namespace = original_parse_args(parser, *args, **kwargs)
            if hasattr(namespace, "skiprows"):
                namespace.skiprows = int(namespace.skiprows)
            return namespace

        argparse.ArgumentParser.parse_args = parse_args_with_integer_skiprows
    try:
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise
    finally:
        if original_parse_args is not None:
            argparse.ArgumentParser.parse_args = original_parse_args
    paths = save_matplotlib_figures(output_directory)
    if not paths:
        raise RuntimeError("The plotting script completed without producing a figure")


def run_live_data(output_directory, command):
    arguments = [item for item in command[1:] if item not in {"-ps", "-noplot"}]
    completed = subprocess.run(
        [command[0]] + arguments + ["-noplot"], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, universal_newlines=True,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    match = re.search(r"into\s+(\S+\.dat)", completed.stdout)
    quantity = "energies"
    if "-q" in arguments and arguments.index("-q") + 1 < len(arguments):
        quantity = arguments[arguments.index("-q") + 1]
    candidates = [Path(match.group(1))] if match else []
    candidates.extend(sorted(Path(".").glob("{}*.dat".format(quantity))))
    data_path = next((path for path in candidates if path.is_file()), None)
    if data_path is None:
        raise RuntimeError("plot_live_data.sh did not produce a readable data file")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    data_file = "macroscopic_vars.dat"
    if "-f" in arguments and arguments.index("-f") + 1 < len(arguments):
        data_file = arguments[arguments.index("-f") + 1]
    quantity_name = data_path.stem
    extract_script = Path(command[0]).resolve().parent / "extract_live_data.sh"

    def metadata(suffix, default):
        if not extract_script.is_file():
            return default
        result = subprocess.run(
            [str(extract_script), quantity_name + suffix, "-f", data_file],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else default

    raw_lines = [
        line.strip() for line in data_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines() if line.strip()
    ]
    header = raw_lines[0].lstrip("#").split() if raw_lines else []
    first_tokens = raw_lines[0].lstrip("#").split() if raw_lines else []
    try:
        [float(token) for token in first_tokens]
        skip_rows = 0
        header = []
    except ValueError:
        skip_rows = 1
    data = np.loadtxt(str(data_path), comments="#", skiprows=skip_rows)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 2:
        raise RuntimeError("{} has fewer than two numeric columns".format(data_path))
    si_units = "-si" in arguments
    x_suffix = "_xlabel_si" if si_units else "_xlabel"
    y_suffix = "_ylabel_si" if si_units else "_ylabel"
    xlabel = metadata(x_suffix, header[0] if header else "time")
    ylabel = metadata(y_suffix, quantity_name)
    x_factor = 1.0
    y_factor = 1.0
    if si_units:
        try:
            x_factor = float(metadata("_x2si", "1").replace("D", "E").replace("d", "e"))
            y_factor = float(metadata("_y2si", "1").replace("D", "E").replace("d", "e"))
        except ValueError as exc:
            raise RuntimeError("Invalid SI conversion metadata for {}: {}".format(
                quantity_name, exc
            ))
        print(
            "Applied SI conversion for {}: x *= {}, y *= {}".format(
                quantity_name, x_factor, y_factor
            ),
            flush=True,
        )
    first_y = 2 if "-no0" in arguments and data.shape[1] > 2 else 1
    figure, axis = plt.subplots(figsize=(9, 5.5))
    for column in range(first_y, data.shape[1]):
        label = header[column] if column < len(header) else "column {}".format(column + 1)
        axis.plot(data[:, 0] * x_factor, data[:, column] * y_factor, label=label)
    axis.set_title(quantity_name)
    axis.set_xlabel(xlabel)
    axis.set_ylabel(ylabel)
    if "-log" in arguments:
        axis.set_yscale("log")
    axis.grid(True, alpha=.3)
    if axis.lines:
        axis.legend(fontsize="small")
    save_matplotlib_figures(output_directory)


def run_shell(output_directory, command):
    before = {
        path.resolve(): path.stat().st_mtime_ns
        for pattern in ("*.png", "*.ps", "*.pdf")
        for path in Path(".").glob(pattern)
    }
    completed = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode:
        raise SystemExit(completed.returncode)
    artifacts = [
        path for pattern in ("*.png", "*.ps", "*.pdf") for path in Path(".").glob(pattern)
        if path.resolve() not in before or path.stat().st_mtime_ns > before[path.resolve()]
    ]
    for index, artifact in enumerate(artifacts, 1):
        if artifact.suffix.casefold() == ".png":
            shutil.copy2(str(artifact), str(output_directory / "figure-{:03d}.png".format(index)))
        else:
            output = output_directory / "figure-{:03d}-%03d.png".format(index)
            subprocess.run([
                "gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pngalpha",
                "-r130", "-sOutputFile={}".format(output), str(artifact),
            ], check=True)
    if not list(output_directory.glob("*.png")):
        raise RuntimeError("The shell plotting script did not create a PNG, PS, or PDF artifact")


def run_grid(output_directory, command):
    """Reproduce plot_grids.sh with Matplotlib for an interactive figure."""
    os.environ["MPLBACKEND"] = "Agg"
    sys.dont_write_bytecode = True
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    arguments = command[1:]
    name_filter = ""
    if "-o" in arguments and arguments.index("-o") + 1 < len(arguments):
        name_filter = arguments[arguments.index("-o") + 1]
    pattern = "grid_*{}*.dat".format(name_filter) if name_filter else "grid_*.dat"
    grid_paths = sorted(
        Path(".").glob(pattern), key=lambda path: path.stat().st_mtime
    )
    if not grid_paths:
        raise RuntimeError("No grid data found for pattern {}".format(pattern))
    resolution = (1200, 1200)
    if "-r" in arguments and arguments.index("-r") + 1 < len(arguments):
        try:
            parsed = tuple(
                int(value) for value in arguments[arguments.index("-r") + 1].split("x")
            )
            if len(parsed) == 2:
                resolution = parsed
        except ValueError:
            pass
    figure, axis = plt.subplots(
        figsize=(max(resolution[0], 200) / 120.0, max(resolution[1], 200) / 120.0)
    )
    for grid_path in grid_paths:
        data = np.loadtxt(str(grid_path))
        if data.ndim == 1:
            data = data.reshape(1, -1)
        if data.shape[1] < 2:
            print("Skipping {}: fewer than two columns".format(grid_path), flush=True)
            continue
        axis.plot(data[:, 0], data[:, 1], label=grid_path.name)
    if not axis.lines:
        raise RuntimeError("No readable two-column grid data found")
    axis.set_title("JOREK GRIDS")
    axis.set_xlabel("R")
    axis.set_ylabel("Z")
    axis.set_aspect("equal", adjustable="datalim")
    axis.grid(True, alpha=.25)
    axis.legend(fontsize="small")
    save_matplotlib_figures(output_directory)


def main():
    output_directory, mode, command = parse_command_line()
    if mode == "python":
        run_python(output_directory, command)
    elif mode == "live":
        run_live_data(output_directory, command)
    elif mode == "shell":
        run_shell(output_directory, command)
    elif mode == "grid":
        run_grid(output_directory, command)
    else:
        raise SystemExit("unknown capture mode: {}".format(mode))


if __name__ == "__main__":
    main()
