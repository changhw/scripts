"""Shared parsing, editing, processing, and visualization helpers for JOREK panels."""

import bisect
import glob
import math
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from scipy.constants import Boltzmann, elementary_charge, mu_0, proton_mass


ASSIGNMENT = re.compile(r"^\s*([A-Za-z][\w]*(?:\([^)]*\))?)\s*=\s*(.*?)\s*$")
FILE_VALUE = re.compile(r"^(['\"])(.*?)\1\s*,?\s*$")
GAMMA = 5.0 / 3.0
HEAT_TRANSPORT_PARAMETERS = {
    "zk_i_par", "zk_e_par", "zk_par", "zk_perp", "zk_i_perp", "zk_e_perp",
}
HEAT_TRANSPORT_FILE_PARAMETERS = {"zk_i_perp_file", "zk_e_perp_file", "zk_perp_file"}
HEAT_SOURCE_FILE_PARAMETERS = {
    "heatsource_i_file": "heatsource_i",
    "heatsource_e_file": "heatsource_e",
    "heatsource_file": "heatsource",
}
HEAT_SOURCE_SCALAR_PARAMETERS = {
    "heatsource_i": "heatsource_i_file",
    "heatsource_e": "heatsource_e_file",
    "heatsource": "heatsource_file",
}

# Curated operations exposed by both the desktop and browser panels.  Keeping
# their argument schema here prevents the two front ends from drifting apart.
JOREK_OPERATIONS = (
    {"name": "cvt2vtk", "label": "VTK: all variables", "group": "HDF5 to VTK",
     "fields": ("input",)},
    {"name": "cvt2vtkno0", "label": "VTK: omit n=0", "group": "HDF5 to VTK",
     "fields": ("input",)},
    {"name": "cvt2vtksi", "label": "VTK: SI units", "group": "HDF5 to VTK",
     "fields": ("input",)},
    {"name": "cvt2vtkno0si", "label": "VTK: omit n=0, SI units", "group": "HDF5 to VTK",
     "fields": ("input",)},
    {"name": "cvt2vtk_iplane", "label": "VTK: plane", "group": "HDF5 to VTK",
     "fields": ("i_plane", "input", "only")},
    {"name": "cvt2vtksi_iplane", "label": "VTK: plane, SI units", "group": "HDF5 to VTK",
     "fields": ("i_plane", "input", "only")},
    {"name": "cvt2vtkno0_iplane", "label": "VTK: plane, omit n=0", "group": "HDF5 to VTK",
     "fields": ("i_plane", "input", "only")},
    {"name": "cvt2vtk_iplane_nsub", "label": "VTK: plane with nsub", "group": "HDF5 to VTK",
     "fields": ("i_plane", "nsub", "input", "only")},
    {"name": "cvt2vtk_itor", "label": "VTK: toroidal index", "group": "HDF5 to VTK",
     "fields": ("i_tor", "input", "only")},
    {"name": "cvt2vtk_itor_iplane", "label": "VTK: toroidal index + plane",
     "group": "HDF5 to VTK", "fields": ("i_tor", "i_plane", "input", "only")},
    {"name": "jorek_post_all", "label": "Post-process snapshots", "group": "Post-processing",
     "fields": ("ids", "omp_threads")},
    {"name": "jorek_poincare_all", "label": "Generate Poincare data",
     "group": "Post-processing", "fields": ("ids", "control_file", "omp_threads")},
    {"name": "jorek_four_all", "label": "FFT decomposition", "group": "Post-processing",
     "fields": ("ids", "control_file", "omp_threads")},
)

OPERATION_FIELDS = {
    "input": {"label": "JOREK input", "default": "input", "path_kind": "file",
              "help": "Input namelist passed to jorek2vtk."},
    "i_plane": {"label": "Plane index", "default": "1", "integer": True, "minimum": 0},
    "i_tor": {"label": "Toroidal index", "default": "0", "integer": True, "minimum": 0},
    "nsub": {"label": "nsub", "default": "1", "integer": True, "minimum": 1},
    "only": {"label": "Only step(s)", "default": "", "optional": True,
             "help": "Optional value passed to convert2vtk.sh -only."},
    "ids": {"label": "Snapshot IDs", "default": "", "optional": True,
            "help": "Space-separated IDs or ? patterns; blank processes all available snapshots."},
    "control_file": {"label": "Control/input file", "default": "input",
                     "path_kind": "file",
                     "help": "Stdin control file used by jorek2_poincare or jorek2_four."},
    "omp_threads": {
        "label": "OpenMP threads", "default": "", "optional": True,
        "integer": True, "minimum": 1, "environment": "OMP_NUM_THREADS",
        "help": "Optional positive integer exported as OMP_NUM_THREADS.",
    },
}

_OPERATION_BY_NAME = {item["name"]: item for item in JOREK_OPERATIONS}
_ID_ARGUMENT = re.compile(r"^[0-9?]+$")

# Aliases cannot be invoked through a variable in Bash, so the four aliases in
# my_bashrc are expanded explicitly.  Functions are called by name after the
# same bashrc has been sourced.
_OPERATION_SHELL = r'''
source "$1"
operation=$2
shift 2
case "$operation" in
  cvt2vtk)      convert2vtk.sh -j 8 jorek2vtk "$@" ;;
  cvt2vtkno0)   convert2vtk.sh -no0 -j 8 jorek2vtk "$@" ;;
  cvt2vtksi)    convert2vtk.sh -si -j 8 jorek2vtk "$@" ;;
  cvt2vtkno0si) convert2vtk.sh -no0 -si -j 8 jorek2vtk "$@" ;;
  *) "$operation" "$@" ;;
esac
'''


def operation_definitions():
    """Return JSON-friendly command and field definitions for a panel."""
    definitions = []
    for operation in JOREK_OPERATIONS:
        item = dict(operation)
        item["fields"] = []
        for name in operation["fields"]:
            field = dict({"name": name}, **OPERATION_FIELDS[name])
            if field.get("path_kind"):
                field["help"] = "{} Type to autocomplete from the working directory.".format(
                    field.get("help", "")
                ).strip()
            item["fields"].append(field)
        definitions.append(item)
    return definitions


def _validated_operation_args(operation, values):
    if operation not in _OPERATION_BY_NAME:
        raise ValueError("Unknown JOREK operation: {}".format(operation))
    values = values or {}
    arguments = []
    for field_name in _OPERATION_BY_NAME[operation]["fields"]:
        field = OPERATION_FIELDS[field_name]
        value = str(values.get(field_name, field.get("default", ""))).strip()
        if not value:
            if field.get("optional"):
                continue
            raise ValueError("{} is required".format(field["label"]))
        if field.get("integer"):
            try:
                number = int(value)
            except ValueError:
                raise ValueError("{} must be an integer".format(field["label"]))
            if number < field.get("minimum", number):
                raise ValueError(
                    "{} must be at least {}".format(field["label"], field["minimum"])
                )
            value = str(number)
        if field.get("environment"):
            continue
        if field_name == "ids":
            try:
                id_arguments = shlex.split(value)
            except ValueError as exc:
                raise ValueError("Invalid snapshot IDs: {}".format(exc))
            invalid = [item for item in id_arguments if not _ID_ARGUMENT.match(item)]
            if invalid:
                raise ValueError(
                    "Snapshot IDs may contain only digits and ?: {}".format(invalid[0])
                )
            arguments.extend(id_arguments)
        else:
            arguments.append(value)
    if operation in {"jorek_poincare_all", "jorek_four_all"}:
        control_file = arguments.pop()
        arguments.extend(["-fn", control_file])
    return arguments


def operation_environment(operation, values=None, base_environment=None):
    """Build the child environment, including optional operation settings."""
    _validated_operation_args(operation, values)
    environment = dict(os.environ if base_environment is None else base_environment)
    values = values or {}
    for field_name in _OPERATION_BY_NAME[operation]["fields"]:
        field = OPERATION_FIELDS[field_name]
        variable = field.get("environment")
        value = str(values.get(field_name, field.get("default", ""))).strip()
        if variable and value:
            environment[variable] = str(int(value)) if field.get("integer") else value
    return environment


def jorek_operation_command(operation, values=None, bashrc_path=None):
    """Build a safe argv list for one curated my_bashrc operation."""
    arguments = _validated_operation_args(operation, values)
    if bashrc_path is None:
        bashrc_path = os.environ.get(
            "JOREK_BASHRC", str(Path(__file__).resolve().parent.parent / "my_bashrc")
        )
    bashrc_path = Path(bashrc_path).expanduser().resolve()
    if not bashrc_path.is_file():
        raise ValueError("JOREK bashrc not found: {}".format(bashrc_path))
    return [
        "bash", "--noprofile", "--norc", "-O", "expand_aliases", "-c",
        _OPERATION_SHELL, "jorek-panel", str(bashrc_path), operation,
    ] + arguments


def format_operation_command(operation, values=None):
    """Return the concise command users recognize from my_bashrc."""
    assignments = operation_environment(operation, values, {})
    return " ".join(
        shlex.quote(item)
        for item in (
            ["{}={}".format(name, value) for name, value in assignments.items()]
            + [operation] + _validated_operation_args(operation, values)
        )
    )


PLOT_FIELDS = {
    "files": {"label": "Input file(s)", "default": "postproc/exprs_midplane_s*.dat",
              "multi": True, "positional": True, "path_kind": "file",
              "help": "Space-separated paths or glob patterns."},
    "vtk_files": {"label": "VTK file(s)", "default": "vtk*/jorek.*.vtk", "multi": True,
                  "positional": True, "path_kind": "file",
                  "help": "Space-separated paths or glob patterns."},
    "poincare_files": {"label": "Poincare file(s)",
                       "default": "poincares/poinc_R-Z_s*.dat", "multi": True,
                       "positional": True, "path_kind": "file",
                       "help": "Space-separated paths or glob patterns."},
    "folder": {"label": "Results folder", "default": "four_results",
               "positional": True, "path_kind": "directory"},
    "directory": {"label": "Data directory", "default": "postproc", "flag": "-fp",
                  "path_kind": "directory"},
    "data_file": {"label": "Macroscopic data", "default": "macroscopic_vars.dat",
                  "flag": "-f", "path_kind": "file"},
    "grid_filter": {"label": "Grid name filter", "default": "", "flag": "-o",
                    "optional": True, "help": "For example: initial or xpoint."},
    "resolution": {"label": "PNG resolution", "default": "1200x1200", "flag": "-r"},
    "quantity": {"label": "Quantity", "default": "energies", "flag": "-q"},
    "variables": {"label": "Variables", "default": "rho", "multi": True, "flag": "-va"},
    "steps": {"label": "Steps", "default": "000000", "multi": True, "flag": "-st"},
    "m_modes": {"label": "m modes", "default": "1", "multi": True, "flag": "-ml"},
    "n_modes": {"label": "n modes", "default": "1", "multi": True, "flag": "-nl"},
    "file_prefix": {"label": "File prefix", "default": "exprs_midplane_s", "flag": "-fn"},
    "column": {"label": "Y column", "default": "1", "flag": "-yc"},
    "x_column": {"label": "X column", "default": "0", "flag": "-xc"},
    "skip_rows": {"label": "Header rows", "default": "1", "flag": "-sk"},
    "method_vtk": {"label": "Plot method", "default": "it", "flag": "-me",
                   "choices": ("it", "sc", "sf", "ff")},
    "method_modes": {"label": "Mode view", "default": "al", "flag": "-me",
                     "choices": ("am", "cm", "ph", "al")},
    "x_scale": {"label": "X scale", "default": "linear", "flag": "-xs",
                "choices": ("linear", "log")},
    "y_scale": {"label": "Y scale", "default": "linear", "flag": "-ys",
                "choices": ("linear", "log")},
    "title": {"label": "Title", "default": "", "flag": "-ti", "optional": True},
    "xlabel": {"label": "X label", "default": "", "flag": "-xl", "optional": True},
    "ylabel": {"label": "Y label", "default": "", "flag": "-yl", "optional": True},
    "xlim": {"label": "X limits", "default": "", "multi": True, "flag": "-xlim",
             "optional": True, "help": "Two space-separated values."},
    "ylim": {"label": "Y limits", "default": "", "multi": True, "flag": "-ylim",
             "optional": True, "help": "Two space-separated values."},
    "xylim": {"label": "R/Z limits", "default": "", "multi": True, "flag": "-xylim",
              "optional": True, "help": "xmin xmax ymin ymax."},
    "clim": {"label": "Color limits", "default": "", "multi": True, "flag": "-clim",
             "optional": True, "help": "Minimum and maximum."},
    "contours": {"label": "Contour values", "default": "1.0", "multi": True, "flag": "-cs"},
    "q_surfaces": {"label": "q surfaces", "default": "", "multi": True, "flag": "-qs",
                   "optional": True},
    "time_multiplier": {
        "label": "Time multiplier", "default": "1.0", "flag": "-tm",
        "help": "Use $time2si (or t_JOREK) to derive the multiplier from the active input.",
    },
    "x_multiplier": {
        "label": "X multiplier", "default": "1.0", "flag": "-xm",
        "help": "Use $time2si (or t_JOREK) to derive the value from the active input.",
    },
    "y_multiplier": {
        "label": "Y multiplier", "default": "1.0", "flag": "-ym",
        "help": "Use $time2si (or t_JOREK) to derive the value from the active input.",
    },
    "radial_power": {"label": "Radial power", "default": "1.0", "flag": "-rp"},
    "q_cut": {"label": "q cutoff", "default": "1.3", "flag": "-qc"},
    "time_slice": {"label": "Time slice", "default": "", "flag": "-tslc", "optional": True},
    "radial_slice": {"label": "Radial slice", "default": "", "flag": "-rslc",
                     "optional": True},
    "reference": {"label": "Reference VTK", "default": "", "flag": "-re",
                  "optional": True, "path_kind": "file"},
    "poincare_overlay": {"label": "Poincare overlay", "default": "", "flag": "-pc",
                         "optional": True, "path_kind": "file"},
    "si": {"label": "SI units", "default": "false", "flag": "-si", "boolean": "flag"},
    "no0": {"label": "Omit n=0", "default": "false", "flag": "-no0", "boolean": "flag"},
    "log": {"label": "Log Y", "default": "true", "flag": "-log", "false_flag": "-nolog",
            "boolean": "either"},
    "colorful": {"label": "Colorful", "default": "false", "flag": "-cful",
                 "boolean": "value"},
    "normalize": {"label": "Normalize", "default": "false", "flag": "-norm",
                  "boolean": "value"},
    "extra_args": {"label": "Additional arguments", "default": "", "multi": True,
                   "positional": True, "optional": True,
                   "help": "Advanced CLI arguments; $time2si is replaced by t_JOREK."},
}

JOREK_PLOTS = (
    {"name": "plot_live_data", "script": "plot_live_data.sh", "label": "Live/run history",
     "mode": "live", "fields": ("quantity", "data_file", "si", "no0", "log", "title",
                                "extra_args")},
    {"name": "plot_grid", "script": "plot_grids.sh", "label": "Computational grid",
     "mode": "grid",
     "fields": ("grid_filter", "resolution", "extra_args")},
    {"name": "plot_vtk", "script": "plot_vtk.py", "label": "VTK fields",
     "mode": "python", "fields": ("vtk_files", "variables", "method_vtk", "reference",
                                  "xylim", "clim", "q_surfaces", "time_multiplier",
                                  "poincare_overlay", "extra_args")},
    {"name": "plot_multiple_files", "script": "plot_multiple_files.py",
     "label": "ASCII files", "mode": "python",
     "fields": ("files", "x_column", "column", "skip_rows", "x_scale", "y_scale",
                "title", "xlabel", "ylabel", "xlim", "ylim", "x_multiplier",
                "y_multiplier", "extra_args")},
    {"name": "plot_q_versus_time", "script": "plot_q_versus_time.py",
     "label": "q versus time", "mode": "python",
     "fields": ("directory", "q_cut", "contours", "xlim", "ylim", "clim",
                "time_multiplier", "time_slice", "radial_slice", "extra_args")},
    {"name": "plot_f_versus_time", "script": "plot_f_versus_time.py",
     "label": "Field versus time", "mode": "python",
     "fields": ("directory", "file_prefix", "column", "skip_rows", "radial_power",
                "contours", "xlim", "ylim", "clim", "x_multiplier", "time_slice",
                "radial_slice", "extra_args")},
    {"name": "plot_mn_mode_structures", "script": "plot_mn_mode_structures.py",
     "label": "FFT m/n mode structures", "mode": "python",
     "fields": ("folder", "variables", "steps", "m_modes", "n_modes", "method_modes",
                "y_multiplier", "time_multiplier", "normalize", "xlim", "ylim",
                "extra_args")},
    {"name": "plot_poincare_all", "script": "plot_poincare_all.py",
     "label": "Poincare plots", "mode": "python",
     "fields": ("poincare_files", "time_multiplier", "colorful", "title", "xylim",
                "extra_args")},
)

_PLOT_BY_NAME = {item["name"]: item for item in JOREK_PLOTS}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def resolve_jorek_utility(script_name):
    """Locate a JOREK utility script without assuming the current directory."""
    candidates = []
    configured = os.environ.get("JOREK_UTIL")
    if configured:
        candidates.append(Path(configured).expanduser() / script_name)
    candidates.extend((
        Path(__file__).resolve().parent.parent / "util" / script_name,
        Path.home() / "jorek" / "util" / script_name,
    ))
    executable = shutil.which(script_name)
    if executable:
        candidates.append(Path(executable))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def plot_definitions():
    """Return plotting definitions plus current script availability."""
    result = []
    for plot in JOREK_PLOTS:
        item = dict(plot)
        script_path = resolve_jorek_utility(plot["script"])
        item["available"] = script_path is not None
        item["script_path"] = str(script_path) if script_path else ""
        item["fields"] = []
        for name in plot["fields"]:
            field = dict({"name": name}, **PLOT_FIELDS[name])
            if field.get("path_kind"):
                field["help"] = "{} Type to autocomplete from the working directory.".format(
                    field.get("help", "")
                ).strip()
            item["fields"].append(field)
        result.append(item)
    return result


def _split_plot_value(label, value):
    try:
        return shlex.split(value)
    except ValueError as exc:
        raise ValueError("Invalid {}: {}".format(label, exc))


def path_completions(directory, value, kind="file", multi=False, limit=50):
    """Return safe relative path completions rooted at a working directory."""
    if kind not in {"file", "directory"}:
        return []
    try:
        root = Path(directory).expanduser().resolve(strict=True)
    except OSError:
        return []
    if not root.is_dir():
        return []

    text = str(value or "")
    token_start = 0
    if multi:
        escaped = False
        quote = None
        for index, character in enumerate(text):
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif quote:
                if character == quote:
                    quote = None
            elif character in {"'", '"'}:
                quote = character
            elif character.isspace():
                token_start = index + 1
    token = text[token_start:]
    quote_prefix = token[0] if token[:1] in {"'", '"'} else ""
    if quote_prefix:
        token = token[1:]
    unescaped = token.replace(r"\ ", " ")
    parent_text, prefix = os.path.split(unescaped)
    search_directory = root / parent_text if parent_text else root
    try:
        search_directory = search_directory.resolve(strict=True)
        search_directory.relative_to(root)
    except (OSError, ValueError):
        return []
    if not search_directory.is_dir():
        return []

    suggestions = []
    try:
        children = sorted(
            search_directory.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    except OSError:
        return []
    for child in children:
        if not child.name.casefold().startswith(prefix.casefold()):
            continue
        if child.name.startswith(".") and not prefix.startswith("."):
            continue
        if kind == "directory" and not child.is_dir():
            continue
        relative = child.relative_to(root).as_posix()
        if child.is_dir():
            relative += "/"
        if multi:
            relative = relative.replace(" ", r"\ ")
        suggestions.append(text[:token_start] + quote_prefix + relative)
        if len(suggestions) >= limit:
            break
    return suggestions


def _plot_arguments(plot_name, values, glob_directory=None):
    if plot_name not in _PLOT_BY_NAME:
        raise ValueError("Unknown JOREK plot: {}".format(plot_name))
    values = values or {}
    arguments = list(_PLOT_BY_NAME[plot_name].get("forced_args", ()))
    for field_name in _PLOT_BY_NAME[plot_name]["fields"]:
        field = PLOT_FIELDS[field_name]
        value = str(values.get(field_name, field.get("default", ""))).strip()
        if not value and field.get("optional"):
            continue
        if not value:
            raise ValueError("{} is required".format(field["label"]))
        if field.get("choices") and value not in field["choices"]:
            raise ValueError(
                "{} must be one of {}".format(field["label"], ", ".join(field["choices"]))
            )
        if field.get("boolean"):
            lowered = value.casefold()
            if lowered not in _TRUE_VALUES | _FALSE_VALUES:
                raise ValueError("{} must be true or false".format(field["label"]))
            enabled = lowered in _TRUE_VALUES
            if field["boolean"] == "flag":
                if enabled:
                    arguments.append(field["flag"])
            elif field["boolean"] == "either":
                arguments.append(field["flag"] if enabled else field["false_flag"])
            else:
                arguments.extend([field["flag"], "true" if enabled else "false"])
            continue
        parts = _split_plot_value(field["label"], value) if field.get("multi") else [value]
        if (
            glob_directory is not None and field.get("path_kind") == "file"
            and field.get("multi")
        ):
            expanded = []
            root = Path(glob_directory).expanduser().resolve()
            for part in parts:
                pattern = Path(part).expanduser()
                search_pattern = pattern if pattern.is_absolute() else root / pattern
                matches = (
                    sorted(
                        match for match in glob.glob(str(search_pattern))
                        if Path(match).is_file()
                    )
                    if glob.has_magic(part) else []
                )
                if matches:
                    for match in matches:
                        match_path = Path(match)
                        try:
                            expanded.append(match_path.relative_to(root).as_posix())
                        except ValueError:
                            expanded.append(str(match_path))
                else:
                    expanded.append(part)
            parts = expanded
        if field.get("flag"):
            if plot_name == "plot_live_data" and field_name == "title":
                flag = "-title"
            elif plot_name == "plot_q_versus_time" and field_name == "time_multiplier":
                flag = "-xm"
            else:
                flag = field["flag"]
            arguments.append(flag)
        arguments.extend(parts)
    return arguments


def resolve_plot_values(values, parameter_values=None):
    """Resolve panel conveniences such as $time2si into utility CLI values."""
    resolved = dict(values or {})
    aliases = {"$time2si", "time2si", "$t_jorek", "t_jorek"}
    constants = None

    def time_multiplier():
        nonlocal constants
        if constants is None:
            constants = normalization_constants(parameter_values or {})
        if constants is None:
            raise ValueError(
                "$time2si requires central_density and central_mass in the active input"
            )
        return "{:.12g}".format(constants[1])

    for field_name, field_value in list(resolved.items()):
        text = str(field_value)
        if "multiplier" in field_name.casefold() and text.strip().casefold() in aliases:
            resolved[field_name] = time_multiplier()
        elif field_name == "extra_args" and text:
            parts = _split_plot_value(PLOT_FIELDS["extra_args"]["label"], text)
            replacement = None
            expanded = []
            for part in parts:
                if part.casefold() in aliases:
                    replacement = replacement or time_multiplier()
                    expanded.append(replacement)
                    continue
                if re.search(r"\$(?:time2si|t_jorek)\b", part, flags=re.IGNORECASE):
                    replacement = replacement or time_multiplier()
                    part = re.sub(
                        r"\$(?:time2si|t_jorek)\b", replacement, part,
                        flags=re.IGNORECASE,
                    )
                expanded.append(part)
            resolved[field_name] = shlex.join(expanded)
    return resolved


def jorek_plot_command(
    plot_name, values, output_directory, parameter_values=None,
    working_directory=None,
):
    """Build a headless plot-capture command for a JOREK utility script."""
    if plot_name not in _PLOT_BY_NAME:
        raise ValueError("Unknown JOREK plot: {}".format(plot_name))
    plot = _PLOT_BY_NAME[plot_name]
    script = resolve_jorek_utility(plot["script"])
    if script is None:
        raise ValueError(
            "{} was not found. Set JOREK_UTIL or restore it in the JOREK util directory."
            .format(plot["script"])
        )
    output_directory = Path(output_directory).expanduser().resolve()
    runner = Path(__file__).resolve().parent / "jorek_plot_capture.py"
    if not runner.is_file():
        raise ValueError("Plot capture helper not found: {}".format(runner))
    return [
        sys.executable, "-u", str(runner), "--output-dir", str(output_directory),
        "--mode", plot["mode"], "--", str(script),
    ] + _plot_arguments(
        plot_name, resolve_plot_values(values, parameter_values),
        glob_directory=working_directory if working_directory is not None else Path.cwd(),
    )


def format_plot_command(plot_name, values=None, parameter_values=None):
    """Return the utility command shown in panel previews."""
    plot = _PLOT_BY_NAME.get(plot_name)
    if not plot:
        raise ValueError("Unknown JOREK plot: {}".format(plot_name))
    return " ".join(
        shlex.quote(item)
        for item in [plot["script"]] + _plot_arguments(
            plot_name, resolve_plot_values(values, parameter_values)
        )
    )


def parse_float(value):
    return float(value.replace("D", "E").replace("d", "e"))


def strip_comment(line):
    quote = None
    result = []
    for char in line:
        if char in "'\"":
            quote = None if quote == char else char if quote is None else quote
        if char == "!" and quote is None:
            break
        result.append(char)
    return "".join(result).strip()


def parse_namelist(path):
    parameters = []
    section = ""
    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = strip_comment(raw)
        if not line:
            continue
        if line.startswith("&"):
            section = line[1:].strip()
            continue
        if line == "/":
            section = ""
            continue
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        name, value = match.groups()
        value = value.rstrip(",").strip()
        file_match = FILE_VALUE.match(value)
        referenced_file = file_match.group(2) if file_match and name.casefold().endswith("file") else None
        parameters.append({"section": section, "name": name, "value": value,
                           "file": referenced_file, "line": line_number})
    return parameters


def parameter_map(parameters):
    return {str(item["name"]).casefold(): str(item["value"]) for item in parameters}


def density_constants(values):
    try:
        number_density = parse_float(values["central_density"]) * 1e20
        mass_density = parse_float(values["central_mass"]) * number_density * proton_mass
    except (KeyError, ValueError):
        return None
    return (number_density, mass_density) if mass_density > 0 else None


def normalization_constants(values):
    densities = density_constants(values)
    if not densities:
        return None
    velocity = 1.0 / math.sqrt(mu_0 * densities[1])
    return velocity, 1000.0 / velocity


def value_in_si(name, value, values=None):
    try:
        fj = parse_float(value)
    except (TypeError, ValueError):
        return "--"
    key = name.casefold()
    if key == "central_density":
        return "{:.8e} m^-3".format(fj * 1e20)
    if key == "central_mass":
        return "{:.8e} kg".format(fj * proton_mass)
    if key == "i_target":
        return "{:.8e} A".format(fj)
    if key == "particlesource" and fj == 0:
        return "--"
    contextual = ({"eta", "eta_ohmic", "visco", "visco_par", "visco_par_par",
                   "d_perp", "d_par", "particlesource"}
                  | HEAT_TRANSPORT_PARAMETERS | set(HEAT_SOURCE_SCALAR_PARAMETERS))
    if key not in contextual or not values:
        return "--"
    densities = density_constants(values)
    if not densities:
        return "--"
    rho0 = densities[1]
    if key in {"eta", "eta_ohmic"}:
        return "{:.8e} Ohm m".format(fj * math.sqrt(mu_0 / rho0))
    if key in {"d_perp", "d_par"}:
        return "{:.8e} m^2 s^-1".format(fj / math.sqrt(mu_0 * rho0))
    if key == "particlesource":
        return "{:.8e} kg s^-1 m^-3".format(fj * math.sqrt(rho0 / mu_0))
    if key in HEAT_SOURCE_SCALAR_PARAMETERS:
        if HEAT_SOURCE_SCALAR_PARAMETERS[key] in values:
            return "--"
        result = fj / ((GAMMA - 1) * mu_0 * math.sqrt(mu_0 * rho0))
        return "{:.8e} W m^-3".format(result)
    if key in HEAT_TRANSPORT_PARAMETERS:
        coefficient = fj * math.sqrt(rho0 / mu_0) / (GAMMA - 1)
        return "kappa={:.8e} kg m^-1 s^-1; chi={:.8e} m^2 s^-1".format(coefficient, coefficient / rho0)
    dynamic = fj * math.sqrt(rho0 / mu_0)
    return "mu={:.8e} kg m^-1 s^-1; nu={:.8e} m^2 s^-1".format(dynamic, dynamic / rho0)


def canonical_value(value):
    cleaned = value.rstrip(",").strip()
    lowered = cleaned.casefold()
    if lowered in {".true.", ".t."}:
        return True
    if lowered in {".false.", ".f."}:
        return False
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "'\"":
        return cleaned[1:-1]
    try:
        if "," in cleaned:
            return tuple(parse_float(token.strip()) for token in cleaned.split(",") if token.strip())
        return parse_float(cleaned)
    except ValueError:
        return " ".join(cleaned.split()).casefold()


def read_numeric_file(path):
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = []
    for raw in raw_lines:
        line = strip_comment(raw).replace("D", "E").replace("d", "e")
        if not line:
            continue
        try:
            row = [float(token) for token in line.replace(",", " ").split()]
        except ValueError:
            continue
        if row:
            rows.append(row)
    return rows, raw_lines


def interpolate(source_x, source_y, target_x):
    if len(source_x) != len(source_y) or not source_x:
        raise ValueError("Interpolation source must contain matching x and y values")
    points = sorted(zip(source_x, source_y))
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    result = []
    for target in target_x:
        index = bisect.bisect_left(xs, target)
        if index == 0:
            result.append(ys[0])
        elif index == len(xs):
            result.append(ys[-1])
        else:
            x0, x1, y0, y1 = xs[index - 1], xs[index], ys[index - 1], ys[index]
            result.append(y0 + (target - x0) / (x1 - x0) * (y1 - y0) if x1 != x0 else y0)
    return result


def inline_boundary(parameters):
    values = parameter_map(parameters)
    names = ("r_boundary", "z_boundary", "psi_boundary")
    if not all(name in values for name in names):
        return None
    try:
        columns = [[parse_float(token.strip()) for token in values[name].split(",") if token.strip()]
                   for name in names]
    except ValueError:
        return None
    count = min(len(column) for column in columns)
    return [[columns[0][i], columns[1][i], columns[2][i]] for i in range(count)]


def replace_assignment_value(line, name, new_value):
    ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
    body = line[:-len(ending)] if ending else line
    quote, comment_index = None, len(body)
    for index, char in enumerate(body):
        if char in "'\"":
            quote = None if quote == char else char if quote is None else quote
        if char == "!" and quote is None:
            comment_index = index
            break
    code, comment = body[:comment_index], body[comment_index:]
    equals = code.find("=")
    if equals < 0 or code[:equals].strip().casefold() != name.casefold():
        raise ValueError("Line is not an assignment for {}".format(name))
    after = code[equals + 1:]
    leading = after[:len(after) - len(after.lstrip())]
    trailing = after[len(after.rstrip()):]
    old_value = after[len(leading):len(after) - len(trailing)]
    # Keep a trailing comma so following namelist entries stay separated.
    comma = "" if new_value.rstrip().endswith(",") else "," if old_value.endswith(",") else ""
    return code[:equals + 1] + leading + new_value + comma + trailing + comment + ending


def update_parameter(path, line_number, name, new_value):
    original_mode = stat.S_IMODE(path.stat().st_mode)
    with path.open("r", encoding="utf-8", errors="replace", newline="") as source:
        lines = source.readlines()
    if not 1 <= line_number <= len(lines):
        raise ValueError("Invalid line number")
    lines[line_number - 1] = replace_assignment_value(lines[line_number - 1], name, new_value)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False,
                                         dir=path.parent, prefix=".jorek-web-", suffix=".tmp") as temporary:
            temporary.writelines(lines)
            temporary_name = temporary.name
        os.chmod(temporary_name, original_mode)
        os.replace(temporary_name, str(path))
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
