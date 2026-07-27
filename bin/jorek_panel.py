#!/usr/bin/env python3
"""Desktop viewer for a JOREK input namelist and its referenced profile files."""

import argparse
import bisect
import math
import os
import re
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional, Set, Tuple

from scipy.constants import (
    Boltzmann as BOLTZMANN_CONSTANT,
    elementary_charge as ELEMENTARY_CHARGE,
    mu_0 as VACUUM_PERMEABILITY,
    proton_mass as PROTON_MASS_KG,
)

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
except ImportError as exc:  # pragma: no cover - handled at application startup
    FigureCanvasTkAgg = NavigationToolbar2Tk = Figure = None
    MATPLOTLIB_ERROR = exc
else:
    MATPLOTLIB_ERROR = None


ASSIGNMENT = re.compile(r"^\s*([A-Za-z][\w]*(?:\([^)]*\))?)\s*=\s*(.*?)\s*$")
FILE_VALUE = re.compile(r"^(['\"])(.*?)\1\s*,?\s*$")
ADIABATIC_INDEX = 5 / 3
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


def parse_fortran_float(value: str) -> float:
    """Parse a scalar Fortran number, including D-exponent notation."""
    return float(value.replace("D", "E").replace("d", "e"))


def parse_fortran_float_list(value: str) -> List[float]:
    """Parse a comma-separated list of Fortran floating-point values."""
    return [parse_fortran_float(token.strip()) for token in value.rstrip(",").split(",") if token.strip()]


def canonical_parameter_value(value: str) -> object:
    """Normalize common Fortran spellings before comparing input values."""
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
            return tuple(parse_fortran_float_list(cleaned))
        return parse_fortran_float(cleaned)
    except ValueError:
        return " ".join(cleaned.split()).casefold()


def inline_boundary_rows(parameters: List[Dict[str, object]]) -> Optional[List[List[float]]]:
    """Build R/Z/Psi rows from active inline boundary lists, when present."""
    values = {str(item["name"]).casefold(): str(item["value"]) for item in parameters}
    names = ("r_boundary", "z_boundary", "psi_boundary")
    if not all(name in values for name in names):
        return None
    try:
        columns = [parse_fortran_float_list(values[name]) for name in names]
    except ValueError:
        return None
    point_count = min(len(column) for column in columns)
    return [[columns[0][i], columns[1][i], columns[2][i]] for i in range(point_count)]


def value_in_si(name: str, value: str, parameter_values: Optional[Dict[str, str]] = None) -> str:
    """Return the parameter value in SI units.

    A dash indicates that no conversion rule has been supplied for the
    parameter yet.
    """
    try:
        jorek_value = parse_fortran_float(value)
    except ValueError:
        return "—"

    if name.casefold() == "central_density":
        return f"{jorek_value * 1e20:.8e} m⁻³"
    if name.casefold() == "central_mass":
        return f"{jorek_value * PROTON_MASS_KG:.8e} kg"
    normalized_name = name.casefold()
    if normalized_name == "i_target":
        return f"{jorek_value:.8e} A"
    if normalized_name == "particlesource" and jorek_value == 0:
        return "—"
    if normalized_name in {
        "eta", "eta_ohmic", "visco", "visco_par", "visco_par_par",
        "d_perp", "d_par", "particlesource",
        *HEAT_SOURCE_SCALAR_PARAMETERS,
        *HEAT_TRANSPORT_PARAMETERS,
    }:
        if not parameter_values:
            return "—"
        try:
            mass_number = parse_fortran_float(parameter_values["central_mass"])
            density_jorek = parse_fortran_float(parameter_values["central_density"])
        except (KeyError, ValueError):
            return "—"
        rho_0 = mass_number * density_jorek * 1e20 * PROTON_MASS_KG
        if rho_0 <= 0:
            return "—"
        if normalized_name in {"eta", "eta_ohmic"}:
            resistivity = jorek_value * math.sqrt(VACUUM_PERMEABILITY / rho_0)
            return f"{resistivity:.8e} Ω m"
        if normalized_name in {"d_perp", "d_par"}:
            diffusivity = jorek_value / math.sqrt(VACUUM_PERMEABILITY * rho_0)
            return f"{diffusivity:.8e} m² s⁻¹"
        if normalized_name == "particlesource":
            particle_source = jorek_value * math.sqrt(rho_0 / VACUUM_PERMEABILITY)
            return f"{particle_source:.8e} kg s⁻¹ m⁻³"
        if normalized_name in HEAT_SOURCE_SCALAR_PARAMETERS:
            matching_file = HEAT_SOURCE_SCALAR_PARAMETERS[normalized_name]
            if matching_file in parameter_values:
                return "—"
            heat_source = jorek_value / (
                (ADIABATIC_INDEX - 1)
                * VACUUM_PERMEABILITY
                * math.sqrt(VACUUM_PERMEABILITY * rho_0)
            )
            return f"{heat_source:.8e} W m⁻³"
        if normalized_name in HEAT_TRANSPORT_PARAMETERS:
            heat_transport = (
                jorek_value * math.sqrt(rho_0 / VACUUM_PERMEABILITY)
                / (ADIABATIC_INDEX - 1)
            )
            heat_diffusivity = heat_transport / rho_0
            return (
                f"κ={heat_transport:.8e} kg m⁻¹ s⁻¹; "
                f"χ={heat_diffusivity:.8e} m² s⁻¹"
            )
        dynamic_viscosity = jorek_value * math.sqrt(rho_0 / VACUUM_PERMEABILITY)
        kinematic_viscosity = dynamic_viscosity / rho_0
        return (
            f"μ={dynamic_viscosity:.8e} kg m⁻¹ s⁻¹; "
            f"ν={kinematic_viscosity:.8e} m² s⁻¹"
        )
    return "—"


def jorek_normalization_constants(parameter_values: Dict[str, str]) -> Optional[Tuple[float, float]]:
    """Return (JOREK velocity in m/s, JOREK time in ms)."""
    try:
        mass_number = parse_fortran_float(parameter_values["central_mass"])
        density_jorek = parse_fortran_float(parameter_values["central_density"])
    except (KeyError, ValueError):
        return None
    rho_0 = mass_number * density_jorek * 1e20 * PROTON_MASS_KG
    if rho_0 <= 0:
        return None
    velocity = 1 / math.sqrt(VACUUM_PERMEABILITY * rho_0)
    time_ms = 1000 / velocity
    return velocity, time_ms


def strip_comment(line: str) -> str:
    """Remove a Fortran ! comment, respecting single and double quoted strings."""
    quote = None
    result = []
    for char in line:
        if char in "'\"":
            quote = None if quote == char else char if quote is None else quote
        if char == "!" and quote is None:
            break
        result.append(char)
    return "".join(result).strip()


def replace_assignment_value(line: str, name: str, new_value: str) -> str:
    """Replace one namelist value while preserving layout, comment, and newline."""
    ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
    body = line[:-len(ending)] if ending else line
    quote = None
    comment_index = len(body)
    for index, char in enumerate(body):
        if char in "'\"":
            quote = None if quote == char else char if quote is None else quote
        if char == "!" and quote is None:
            comment_index = index
            break
    code, comment = body[:comment_index], body[comment_index:]
    equals_index = code.find("=")
    if equals_index < 0 or code[:equals_index].strip().casefold() != name.casefold():
        raise ValueError(f"Line is not an assignment for {name}")
    after_equals = code[equals_index + 1:]
    leading_space = after_equals[:len(after_equals) - len(after_equals.lstrip())]
    trailing_space = after_equals[len(after_equals.rstrip()):]
    old_value = after_equals[len(leading_space):len(after_equals) - len(trailing_space)]
    # Keep a trailing comma so following namelist entries stay separated.
    comma = "" if new_value.rstrip().endswith(",") else "," if old_value.endswith(",") else ""
    return code[:equals_index + 1] + leading_space + new_value + comma + trailing_space + comment + ending


def update_namelist_parameter(path: Path, line_number: int, name: str, new_value: str) -> None:
    """Atomically update an existing namelist assignment."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as source:
        lines = source.readlines()
    if not 1 <= line_number <= len(lines):
        raise ValueError(f"Line {line_number} is outside {path.name}")
    lines[line_number - 1] = replace_assignment_value(lines[line_number - 1], name, new_value)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", delete=False, dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp",
        ) as temporary:
            temporary.writelines(lines)
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def parse_namelist(path: Path) -> List[Dict[str, object]]:
    parameters = []  # type: List[Dict[str, object]]
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
        file_match = FILE_VALUE.match(value)
        # JOREK file references conventionally use names ending in "file".
        # Do not mistake other quoted values (for example 'Gears') for paths.
        referenced_file = file_match.group(2) if file_match and name.casefold().endswith("file") else None
        parameters.append(
            {"section": section, "name": name, "value": value.rstrip(",").strip(),
             "file": referenced_file, "line": line_number}
        )
    return parameters


def read_numeric_file(path: Path) -> Tuple[List[List[float]], List[str]]:
    rows = []  # type: List[List[float]]
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for raw in raw_lines:
        line = strip_comment(raw).replace("D", "E").replace("d", "e")
        if not line:
            continue
        try:
            values = [float(token) for token in line.replace(",", " ").split()]
        except ValueError:
            continue
        if values:
            rows.append(values)
    return rows, raw_lines


def interpolate_linear(source_x: List[float], source_y: List[float], target_x: List[float]) -> List[float]:
    """Linearly interpolate y onto target x, clamping beyond source endpoints."""
    if len(source_x) != len(source_y) or not source_x:
        raise ValueError("Interpolation source must contain matching x and y values")
    points = sorted(zip(source_x, source_y))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    result = []
    for target in target_x:
        index = bisect.bisect_left(xs, target)
        if index == 0:
            result.append(ys[0])
        elif index == len(xs):
            result.append(ys[-1])
        else:
            x0, x1 = xs[index - 1], xs[index]
            y0, y1 = ys[index - 1], ys[index]
            fraction = 0.0 if x1 == x0 else (target - x0) / (x1 - x0)
            result.append(y0 + fraction * (y1 - y0))
    return result


class JorekPanel(tk.Tk):
    def __init__(self, input_path: Path, comparison_path: Optional[Path] = None):
        super().__init__()
        self.title("JOREK Input Explorer")
        self.geometry("1280x800")
        self.minsize(900, 600)
        self.input_path = input_path.resolve()
        self.parameters = []  # type: List[Dict[str, object]]
        self.comparison_input_path = None  # type: Optional[Path]
        self.comparison_parameters = []  # type: List[Dict[str, object]]
        self.parameter_row_items = {}  # type: Dict[str, str]
        self.profile_items = {}  # type: Dict[str, Dict[str, object]]
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self._configure_style()
        self._build_ui()
        if comparison_path is None:
            self.load_input(self.input_path)
        else:
            self.load_comparison(self.input_path, comparison_path)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Muted.TLabel", foreground="#5f6b7a")

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(14, 12, 14, 8))
        header.pack(fill="x")
        ttk.Label(header, text="JOREK Input Explorer", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Open input…", command=self.choose_input).pack(side="right")
        ttk.Button(header, text="Compare two…", command=self.choose_comparison_inputs).pack(side="right", padx=6)
        self.path_label = ttk.Label(header, style="Muted.TLabel")
        self.path_label.pack(side="left", padx=18)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        params_tab = ttk.Frame(notebook, padding=8)
        profiles_tab = ttk.Frame(notebook, padding=8)
        notebook.add(params_tab, text="Parameters")
        notebook.add(profiles_tab, text="Referenced profiles")
        self._build_parameters_tab(params_tab)
        self._build_profiles_tab(profiles_tab)
        ttk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken", padding=5).pack(fill="x")

    def _build_parameters_tab(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill="x", pady=(0, 7))
        ttk.Label(controls, text="Filter:").pack(side="left")
        entry = ttk.Entry(controls, textvariable=self.search_var, width=40)
        entry.pack(side="left", padx=6)
        self.search_var.trace_add("write", lambda *_: self.refresh_parameters())
        ttk.Button(controls, text="Clear", command=lambda: self.search_var.set("")).pack(side="left")
        ttk.Button(controls, text="Edit selected…", command=self.edit_selected_parameter).pack(side="left", padx=8)
        columns = ("line", "name", "value", "compare_value", "si_value", "compare_si_value", "section")
        self.parameter_tree = ttk.Treeview(parent, columns=columns, show="headings")
        for col, title, width in (("line", "Line", 65), ("name", "Parameter", 190),
                                  ("value", "JOREK value A", 230), ("compare_value", "JOREK value B", 230),
                                  ("si_value", "SI value A", 260), ("compare_si_value", "SI value B", 260),
                                  ("section", "Section", 80)):
            self.parameter_tree.heading(col, text=title)
            self.parameter_tree.column(
                col, width=width, anchor="w",
                stretch=col in {"value", "compare_value", "si_value", "compare_si_value"},
            )
        self.parameter_tree.tag_configure("different", background="#fff3bf")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.parameter_tree.yview)
        self.parameter_tree.configure(yscrollcommand=scroll.set)
        self.parameter_tree.bind("<Double-1>", lambda _event: self.edit_selected_parameter())
        scroll.pack(side="right", fill="y")
        self.parameter_tree.pack(fill="both", expand=True)

    def _build_profiles_tab(self, parent: ttk.Frame) -> None:
        pane = ttk.Panedwindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, padding=(0, 0, 8, 0))
        right = ttk.Frame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=3)
        ttk.Label(left, text="Files referenced by active parameters").pack(anchor="w", pady=(0, 5))
        self.profile_tree = ttk.Treeview(left, columns=("file", "status"), show="tree headings", height=20)
        self.profile_tree.heading("#0", text="Parameter")
        self.profile_tree.heading("file", text="File")
        self.profile_tree.heading("status", text="Status")
        self.profile_tree.column("#0", width=150)
        self.profile_tree.column("file", width=240)
        self.profile_tree.column("status", width=75, anchor="center")
        self.profile_tree.pack(fill="both", expand=True)
        self.profile_tree.bind("<<TreeviewSelect>>", self.show_selected_profile)

        self.file_info = ttk.Label(right, text="Select a referenced file", style="Muted.TLabel")
        self.file_info.pack(fill="x")
        content = ttk.Panedwindow(right, orient="vertical")
        content.pack(fill="both", expand=True, pady=(6, 0))
        plot_frame = ttk.Frame(content)
        preview_frame = ttk.Labelframe(content, text="Data preview", padding=5)
        content.add(plot_frame, weight=3)
        content.add(preview_frame, weight=1)
        limit_box = ttk.Labelframe(plot_frame, text="Plot x limits", padding=(8, 4))
        limit_box.pack(fill="x", pady=(0, 4))
        self.xmin_var = tk.StringVar()
        self.xmax_var = tk.StringVar()
        ttk.Label(limit_box, text="Minimum:").pack(side="left")
        ttk.Entry(limit_box, textvariable=self.xmin_var, width=12).pack(side="left", padx=(4, 10))
        ttk.Label(limit_box, text="Maximum:").pack(side="left")
        ttk.Entry(limit_box, textvariable=self.xmax_var, width=12).pack(side="left", padx=(4, 10))
        ttk.Button(limit_box, text="Apply", command=self.apply_plot_xlim).pack(side="left")
        ttk.Button(limit_box, text="Reset", command=self.reset_plot_limits).pack(side="left", padx=6)
        self.figure = Figure(figsize=(10, 4), dpi=100)
        self.raw_axes = self.figure.add_subplot(121)
        self.axes = self.figure.add_subplot(122)
        self.secondary_axes = None
        self.raw_axes.grid(True, alpha=.25)
        self.axes.grid(True, alpha=.25)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        NavigationToolbar2Tk(self.canvas, plot_frame, pack_toolbar=True).update()
        self.preview = tk.Text(preview_frame, height=8, wrap="none", font=("Consolas", 9), state="disabled")
        yscroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        xscroll = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.preview.bind("<MouseWheel>", self._scroll_preview)
        self.preview.bind("<Button-4>", lambda event: self.preview.yview_scroll(-1, "units"))
        self.preview.bind("<Button-5>", lambda event: self.preview.yview_scroll(1, "units"))
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.preview.pack(fill="both", expand=True)

    def choose_input(self) -> None:
        selected = filedialog.askopenfilename(title="Select JOREK input", initialdir=self.input_path.parent)
        if selected:
            self.load_input(Path(selected))

    def choose_comparison_inputs(self) -> None:
        selected = filedialog.askopenfilenames(
            title="Select exactly two JOREK inputs", initialdir=self.input_path.parent,
        )
        if not selected:
            return
        if len(selected) != 2:
            messagebox.showerror("Select two inputs", "Please select exactly two input files.")
            return
        self.load_comparison(Path(selected[0]), Path(selected[1]))

    def load_input(self, path: Path) -> None:
        try:
            parameters = parse_namelist(path)
        except OSError as exc:
            messagebox.showerror("Cannot open input", str(exc))
            return
        self.input_path = path.resolve()
        self.parameters = parameters
        self.comparison_input_path = None
        self.comparison_parameters = []
        self.path_label.configure(text=str(self.input_path))
        self.parameter_tree.configure(displaycolumns=("line", "name", "value", "si_value", "section"))
        self.parameter_tree.heading("value", text=f"JOREK value ({self.input_path.name})")
        self.parameter_tree.heading("si_value", text="SI value")
        self.parameter_tree.heading("compare_value", text="JOREK value B")
        self.refresh_parameters()
        self.refresh_profiles()
        self.status_var.set(f"Loaded {len(parameters)} active parameters from {self.input_path.name}")

    def load_comparison(self, first_path: Path, second_path: Path) -> None:
        try:
            first_parameters = parse_namelist(first_path)
            second_parameters = parse_namelist(second_path)
        except OSError as exc:
            messagebox.showerror("Cannot open input", str(exc))
            return
        self.input_path = first_path.resolve()
        self.parameters = first_parameters
        self.comparison_input_path = second_path.resolve()
        self.comparison_parameters = second_parameters
        self.path_label.configure(text=f"A: {self.input_path}   |   B: {self.comparison_input_path}")
        self.parameter_tree.configure(
            displaycolumns=("line", "name", "value", "compare_value", "si_value", "compare_si_value", "section")
        )
        self.parameter_tree.heading("value", text=f"JOREK value A ({self.input_path.name})")
        self.parameter_tree.heading("compare_value", text=f"JOREK value B ({self.comparison_input_path.name})")
        self.parameter_tree.heading("si_value", text="SI value A")
        self.parameter_tree.heading("compare_si_value", text="SI value B")
        self.refresh_parameters()
        self.refresh_profiles()
        self.status_var.set(
            f"Comparing {self.input_path.name} and {self.comparison_input_path.name}; "
            "highlighted rows differ"
        )

    def refresh_parameters(self) -> None:
        query = self.search_var.get().casefold().strip()
        parameter_values = {
            str(item["name"]).casefold(): str(item["value"])
            for item in self.parameters
        }
        comparison_values = {
            str(item["name"]).casefold(): str(item["value"])
            for item in self.comparison_parameters
        }
        self.parameter_tree.delete(*self.parameter_tree.get_children())
        self.parameter_row_items.clear()
        normalization = jorek_normalization_constants(parameter_values)
        comparison_normalization = jorek_normalization_constants(comparison_values)
        if normalization is not None or comparison_normalization is not None:
            derived_definitions = (("v_JOREK", "1 velocity unit", 0, "m s⁻¹"),
                                   ("t_JOREK", "1 time unit", 1, "ms"))
            for name, jorek_value, index, unit in derived_definitions:
                si_value = f"{normalization[index]:.8e} {unit}" if normalization else "—"
                compare_si = (
                    f"{comparison_normalization[index]:.8e} {unit}"
                    if comparison_normalization else "—"
                )
                different = bool(self.comparison_parameters) and si_value != compare_si
                haystack = f"derived constants {name} {jorek_value} {si_value} {compare_si}".casefold()
                if not query or query in haystack:
                    self.parameter_tree.insert(
                        "", "end",
                        values=("—", name, jorek_value, jorek_value if comparison_normalization else "—",
                                si_value, compare_si, "Derived constants"),
                        tags=("different",) if different else (),
                    )

        first_items = {str(item["name"]).casefold(): item for item in self.parameters}
        second_items = {str(item["name"]).casefold(): item for item in self.comparison_parameters}
        ordered_names = list(first_items)
        ordered_names.extend(name for name in second_items if name not in first_items)
        for normalized_name in ordered_names:
            first = first_items.get(normalized_name)
            second = second_items.get(normalized_name)
            item = first or second
            first_value = str(first["value"]) if first else "—"
            second_value = str(second["value"]) if second else "—"
            si_value = value_in_si(str(item["name"]), first_value, parameter_values) if first else "—"
            compare_si = (
                value_in_si(str(item["name"]), second_value, comparison_values) if second else "—"
            )
            different = bool(self.comparison_parameters) and (
                first is None or second is None
                or canonical_parameter_value(first_value) != canonical_parameter_value(second_value)
            )
            first_line = str(first["line"]) if first else "—"
            second_line = str(second["line"]) if second else "—"
            line = first_line if first_line == second_line or not second else f"{first_line}/{second_line}"
            first_section = str(first["section"]) if first else "—"
            second_section = str(second["section"]) if second else "—"
            section = (
                first_section if first_section == second_section or not second
                else f"{first_section}/{second_section}"
            )
            haystack = (
                f"{section} {item['name']} {first_value} {second_value} {si_value} {compare_si}"
            ).casefold()
            if query and query not in haystack:
                continue
            iid = self.parameter_tree.insert(
                "", "end",
                values=(line, item["name"], first_value, second_value, si_value, compare_si, section),
                tags=("different",) if different else (),
            )
            self.parameter_row_items[iid] = normalized_name

    def edit_selected_parameter(self) -> None:
        """Edit the selected existing parameter in input A or input B."""
        selection = self.parameter_tree.selection()
        if not selection or selection[0] not in self.parameter_row_items:
            messagebox.showinfo("Edit parameter", "Select an editable parameter row first.")
            return
        normalized_name = self.parameter_row_items[selection[0]]
        first_items = {str(item["name"]).casefold(): item for item in self.parameters}
        second_items = {str(item["name"]).casefold(): item for item in self.comparison_parameters}
        first = first_items.get(normalized_name)
        second = second_items.get(normalized_name)
        edit_second = False
        if first and second:
            choice = messagebox.askyesnocancel(
                "Choose input", "Edit input A?\n\nYes: input A\nNo: input B\nCancel: do nothing",
            )
            if choice is None:
                return
            edit_second = not choice
        elif second:
            edit_second = True
        item = second if edit_second else first
        path = self.comparison_input_path if edit_second else self.input_path
        if item is None or path is None:
            messagebox.showerror("Edit parameter", "This parameter is not present in the selected input.")
            return
        new_value = simpledialog.askstring(
            "Edit parameter",
            f"{path.name}\n{item['name']} =",
            initialvalue=str(item["value"]), parent=self,
        )
        if new_value is None:
            return
        new_value = new_value.strip()
        if not new_value:
            messagebox.showerror("Invalid value", "The parameter value cannot be empty.")
            return
        try:
            update_namelist_parameter(path, int(item["line"]), str(item["name"]), new_value)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot update input", str(exc))
            return
        if self.comparison_input_path is not None:
            self.load_comparison(self.input_path, self.comparison_input_path)
        else:
            self.load_input(self.input_path)
        self.status_var.set(f"Updated {item['name']} in {path.name}")

    def refresh_profiles(self) -> None:
        self.profile_tree.delete(*self.profile_tree.get_children())
        self.profile_items.clear()
        comparison_files = {
            str(item["name"]).casefold(): item
            for item in self.comparison_parameters if item["file"]
        }
        primary_inline_boundary = inline_boundary_rows(self.parameters)
        comparison_inline_boundary = inline_boundary_rows(self.comparison_parameters)
        primary_file_names = set()  # type: Set[str]
        for item in self.parameters:
            filename = item["file"]
            if not filename:
                continue
            normalized_name = str(item["name"]).casefold()
            primary_file_names.add(normalized_name)
            path = (self.input_path.parent / str(filename)).resolve()
            comparison_item = comparison_files.get(normalized_name)
            comparison_path = (
                (self.comparison_input_path.parent / str(comparison_item["file"])).resolve()
                if comparison_item and self.comparison_input_path else None
            )
            file_display = str(filename)
            if comparison_item:
                file_display += f" | B: {comparison_item['file']}"
            found_a = path.is_file()
            found_b = comparison_path is None or comparison_path.is_file()
            status = "Found" if found_a and found_b else "Missing"
            iid = self.profile_tree.insert("", "end", text=str(item["name"]), values=(file_display, status))
            self.profile_items[iid] = {
                "path": path, "comparison_path": comparison_path, "parameter": item["name"],
            }
            if normalized_name == "r_z_psi_bnd_file" and comparison_path is None and comparison_inline_boundary:
                self.profile_items[iid]["comparison_rows"] = comparison_inline_boundary
        for normalized_name, item in comparison_files.items():
            if (normalized_name in primary_file_names or self.comparison_input_path is None
                    or (normalized_name == "r_z_psi_bnd_file" and primary_inline_boundary)):
                continue
            path = (self.comparison_input_path.parent / str(item["file"])).resolve()
            status = "Found" if path.is_file() else "Missing"
            iid = self.profile_tree.insert(
                "", "end", text=str(item["name"]), values=(f"B only: {item['file']}", status),
            )
            self.profile_items[iid] = {
                "path": path, "comparison_path": None, "parameter": item["name"],
                "primary_is_comparison": True,
            }
        parameter_values = {
            str(item["name"]).casefold(): str(item["value"])
            for item in self.parameters
        }
        if "r_z_psi_bnd_file" not in parameter_values and primary_inline_boundary:
            comparison_boundary_file = comparison_files.get("r_z_psi_bnd_file")
            comparison_boundary_path = (
                self.comparison_input_path.parent / str(comparison_boundary_file["file"])
                if comparison_boundary_file and self.comparison_input_path else None
            )
            point_count = len(primary_inline_boundary)
            iid = self.profile_tree.insert(
                "", "end", text="R/Z/Psi boundary",
                values=("Inline boundary lists", f"{point_count} points"),
            )
            self.profile_items[iid] = {
                "kind": "inline_boundary", "parameter": "inline_boundary",
                "rows": primary_inline_boundary,
                "comparison_rows": comparison_inline_boundary,
                "comparison_path": comparison_boundary_path,
            }

    def show_selected_profile(self, _event=None) -> None:
        selection = self.profile_tree.selection()
        if not selection:
            return
        item = self.profile_items[selection[0]]
        # X limits are per-profile: clear the boxes so stale values cannot mislead.
        self.xmin_var.set("")
        self.xmax_var.set("")
        is_inline_boundary = item.get("kind") == "inline_boundary"
        path = None if is_inline_boundary else Path(item["path"])
        primary_is_comparison = bool(item.get("primary_is_comparison"))
        active_parameters = self.comparison_parameters if primary_is_comparison else self.parameters
        active_input_path = self.comparison_input_path if primary_is_comparison else self.input_path
        if self.secondary_axes is not None:
            self.secondary_axes.remove()
            self.secondary_axes = None
        self.raw_axes.clear()
        self.axes.clear()
        # Axes.clear() preserves aspect settings. Reset the equal R-Z boundary
        # aspect so it cannot distort profiles selected afterward.
        self.raw_axes.set_aspect("auto", adjustable="box")
        self.axes.set_aspect("auto", adjustable="box")
        self.raw_axes.grid(True, alpha=.25)
        self.axes.grid(True, alpha=.25)
        if path is not None and not path.is_file():
            self.file_info.configure(text=f"Missing: {path}")
            self.raw_axes.text(.5, .5, "Referenced file not found", ha="center", va="center", transform=self.raw_axes.transAxes)
            self.axes.text(.5, .5, "No SI profile", ha="center", va="center", transform=self.axes.transAxes)
            self._set_preview("")
            self.figure.suptitle(path.name)
            self.canvas.draw_idle()
            return
        if is_inline_boundary:
            rows = item["rows"]
            raw_lines = ["R_boundary        Z_boundary        Psi_boundary"] + [
                f"{row[0]:.12e}  {row[1]:.12e}  {row[2]:.12e}" for row in rows
            ]
        else:
            try:
                rows, raw_lines = read_numeric_file(path)
            except OSError as exc:
                messagebox.showerror("Cannot read profile", str(exc))
                return
        widths = [len(row) for row in rows]
        columns = min(widths) if widths else 0
        if is_inline_boundary:
            display_name = "Inline R_boundary / Z_boundary / Psi_boundary"
            self.file_info.configure(text=f"{display_name}  •  {len(rows)} points  •  from input namelist")
        else:
            display_name = path.name
            size = path.stat().st_size
            self.file_info.configure(text=f"{display_name}  •  {len(rows)} numeric rows  •  {columns} columns  •  {size:,} bytes")
        if rows and columns >= 2:
            parameter_name = str(item["parameter"]).casefold()
            is_boundary = parameter_name in {"r_z_psi_bnd_file", "inline_boundary"} and columns >= 3
            profile_coordinate = [row[0] for row in rows if len(row) >= columns]
            x = (
                profile_coordinate
                if is_boundary
                else [math.sqrt(value) if value >= 0 else math.nan for value in profile_coordinate]
            )
            if is_boundary:
                boundary_rows = [row for row in rows if len(row) >= 3]
                self.raw_axes.plot(
                    [row[0] for row in boundary_rows],
                    [row[1] for row in boundary_rows],
                    linewidth=1.7,
                )
                self.raw_axes.set_xlabel("R")
                self.raw_axes.set_ylabel("Z")
                self.raw_axes.set_title("Boundary shape")
                self.raw_axes.set_aspect("equal", adjustable="datalim")
            else:
                for column in range(1, columns):
                    raw_y = [row[column] for row in rows if len(row) >= columns]
                    self.raw_axes.plot(x, raw_y, linewidth=1.7, label=f"Column {column + 1}")
                self.raw_axes.set_xlabel(r"$\sqrt{\psi_n}$")
                self.raw_axes.set_ylabel("JOREK value")
                self.raw_axes.set_title("Original profile")
                if columns > 2:
                    self.raw_axes.legend()
            is_heat_transport = parameter_name in HEAT_TRANSPORT_FILE_PARAMETERS
            is_density_profile = parameter_name == "rho_file"
            is_current_source = parameter_name == "jsource_file"
            is_temperature_profile = parameter_name in {"ti_file", "te_file", "t_file"}
            is_heat_source = parameter_name in HEAT_SOURCE_FILE_PARAMETERS
            rho_0 = 0.0
            number_density_0 = 0.0
            if is_heat_transport or is_density_profile or is_temperature_profile or is_heat_source:
                parameter_values = {
                    str(parameter["name"]).casefold(): str(parameter["value"])
                    for parameter in active_parameters
                }
                try:
                    mass_number = parse_fortran_float(parameter_values["central_mass"])
                    density_jorek = parse_fortran_float(parameter_values["central_density"])
                    number_density_0 = density_jorek * 1e20
                    rho_0 = mass_number * number_density_0 * PROTON_MASS_KG
                except (KeyError, ValueError):
                    rho_0 = 0
                is_heat_transport = is_heat_transport and rho_0 > 0
                is_density_profile = is_density_profile and rho_0 > 0
                is_temperature_profile = is_temperature_profile and number_density_0 > 0
                is_heat_source = is_heat_source and rho_0 > 0
            if is_boundary:
                self.axes.plot(
                    range(1, len(boundary_rows) + 1),
                    [row[2] for row in boundary_rows],
                    linewidth=1.7,
                )
                self.axes.set_xlabel("Row ID")
                self.axes.set_ylabel("Psi")
                self.axes.set_title("Psi by boundary row")
                self.file_info.configure(
                    text=self.file_info.cget("text") + "  •  no SI conversion"
                )
            elif is_current_source:
                for column in range(1, columns):
                    current_density = [row[column] for row in rows if len(row) >= columns]
                    suffix = "" if columns == 2 else f" (column {column + 1})"
                    self.axes.plot(x, current_density, linewidth=1.7, label=f"J{suffix}")
                self.axes.set_ylabel("Current density (A m⁻²)")
                if columns > 2:
                    self.axes.legend()
                self.file_info.configure(
                    text=self.file_info.cget("text") + "  •  source values already in SI units"
                )
            elif is_heat_source:
                multiplier_name = HEAT_SOURCE_FILE_PARAMETERS[parameter_name]
                try:
                    source_multiplier = parse_fortran_float(parameter_values[multiplier_name])
                except (KeyError, ValueError):
                    source_multiplier = math.nan
                denominator = (
                    (ADIABATIC_INDEX - 1)
                    * VACUUM_PERMEABILITY
                    * math.sqrt(VACUUM_PERMEABILITY * rho_0)
                )
                for column in range(1, columns):
                    normalized = [row[column] for row in rows if len(row) >= columns]
                    converted_source = [
                        source_multiplier * value / denominator for value in normalized
                    ]
                    suffix = "" if columns == 2 else f" (column {column + 1})"
                    self.axes.plot(x, converted_source, linewidth=1.7, label=f"fₛ{suffix}")
                self.axes.set_ylabel("Heat source (W m⁻³)")
                if columns > 2:
                    self.axes.legend()
                self.file_info.configure(
                    text=self.file_info.cget("text")
                    + f"  •  scaled by {multiplier_name}={source_multiplier:g}"
                )
            elif is_temperature_profile:
                self.secondary_axes = self.axes.twinx()
                for column in range(1, columns):
                    normalized = [row[column] for row in rows if len(row) >= columns]
                    temperature_ev = [
                        value / (ELEMENTARY_CHARGE * VACUUM_PERMEABILITY * number_density_0)
                        for value in normalized
                    ]
                    temperature_k = [
                        value / (BOLTZMANN_CONSTANT * VACUUM_PERMEABILITY * number_density_0)
                        for value in normalized
                    ]
                    suffix = "" if columns == 2 else f" (column {column + 1})"
                    self.axes.plot(x, temperature_ev, linewidth=1.7, label=f"T (eV){suffix}")
                    self.secondary_axes.plot(
                        x, temperature_k, "--", linewidth=1.4,
                        label=f"T (K){suffix}", color="#d97706",
                    )
                self.axes.set_ylabel("Temperature (eV)")
                self.secondary_axes.set_ylabel("Temperature (K)", color="#d97706")
                self.axes.legend(loc="upper left")
                self.secondary_axes.legend(loc="upper right")
                self.file_info.configure(
                    text=self.file_info.cget("text") + "  •  temperature in eV and K"
                )
            elif is_density_profile:
                self.secondary_axes = self.axes.twinx()
                for column in range(1, columns):
                    normalized = [row[column] for row in rows if len(row) >= columns]
                    number_density = [value * number_density_0 for value in normalized]
                    mass_density = [value * rho_0 for value in normalized]
                    suffix = "" if columns == 2 else f" (column {column + 1})"
                    self.axes.plot(x, number_density, linewidth=1.7, label=f"n{suffix}")
                    self.secondary_axes.plot(
                        x, mass_density, "--", linewidth=1.4,
                        label=f"ρ{suffix}", color="#d97706",
                    )
                self.axes.set_ylabel("n (m⁻³)")
                self.secondary_axes.set_ylabel("ρ (kg m⁻³)", color="#d97706")
                self.axes.legend(loc="upper left")
                self.secondary_axes.legend(loc="upper right")
                self.file_info.configure(
                    text=self.file_info.cget("text") + "  •  number and mass density in SI units"
                )
            elif is_heat_transport:
                factor = math.sqrt(rho_0 / VACUUM_PERMEABILITY) / (ADIABATIC_INDEX - 1)
                density_reference = next(
                    (parameter for parameter in active_parameters
                     if str(parameter["name"]).casefold() == "rho_file"),
                    None,
                )
                local_density = None
                if density_reference and density_reference["file"]:
                    density_path = active_input_path.parent / str(density_reference["file"])
                    if density_path.is_file():
                        density_rows, _ = read_numeric_file(density_path)
                        density_rows = [row for row in density_rows if len(row) >= 2]
                        if density_rows:
                            normalized_density = interpolate_linear(
                                [row[0] for row in density_rows],
                                [row[1] for row in density_rows],
                                profile_coordinate,
                            )
                            local_density = [rho_0 * value for value in normalized_density]
                self.secondary_axes = self.axes.twinx()
                for column in range(1, columns):
                    raw_y = [row[column] for row in rows if len(row) >= columns]
                    kappa = [value * factor for value in raw_y]
                    if local_density is None:
                        chi = [math.nan] * len(kappa)
                    else:
                        chi = [
                            value / density if density > 0 else math.nan
                            for value, density in zip(kappa, local_density)
                        ]
                    suffix = "" if columns == 2 else f" (column {column + 1})"
                    self.axes.plot(x, kappa, linewidth=1.7, label=f"κ (kg m⁻¹ s⁻¹){suffix}")
                    self.secondary_axes.plot(
                        x, chi, "--", linewidth=1.4,
                        label=f"χ (m² s⁻¹){suffix}", color="#1f77b4",
                    )
                self.axes.set_ylabel("κ (kg m⁻¹ s⁻¹)")
                self.secondary_axes.set_ylabel("χ (m² s⁻¹)", color="#1f77b4")
                self.axes.legend(loc="upper left")
                self.secondary_axes.legend(loc="upper right")
                self.file_info.configure(
                    text=self.file_info.cget("text")
                    + ("  •  SI χ uses interpolated rho_file" if local_density is not None
                       else "  •  rho_file unavailable; SI χ cannot be calculated")
                )
            else:
                self.axes.text(.5, .5, "No SI profile conversion configured", ha="center", va="center", transform=self.axes.transAxes)
            if not is_boundary:
                self.axes.set_xlabel(r"$\sqrt{\psi_n}$")
                self.axes.set_title("SI profile")
            comparison_path = item.get("comparison_path")
            comparison_rows = item.get("comparison_rows")
            if comparison_rows is not None:
                self._overlay_comparison_profile(
                    parameter_name, comparison_rows, self.comparison_parameters,
                    self.comparison_input_path,
                )
                self.file_info.configure(text=self.file_info.cget("text") + "  •  B: inline boundary")
            elif comparison_path is not None and Path(comparison_path).is_file():
                comparison_rows, _ = read_numeric_file(Path(comparison_path))
                self._overlay_comparison_profile(
                    parameter_name, comparison_rows, self.comparison_parameters,
                    self.comparison_input_path,
                )
                self.file_info.configure(
                    text=self.file_info.cget("text") + f"  •  B: {Path(comparison_path).name}"
                )
            for plot_axis in (self.raw_axes, self.axes, self.secondary_axes):
                if plot_axis is not None and plot_axis.get_legend() is not None:
                    plot_axis.get_legend().set_draggable(True)
            self.figure.suptitle(display_name)
            self.figure.tight_layout()
        else:
            self.raw_axes.text(.5, .5, "No plottable two-column numeric data", ha="center", va="center", transform=self.raw_axes.transAxes)
            self.axes.text(.5, .5, "No SI profile", ha="center", va="center", transform=self.axes.transAxes)
            self.figure.suptitle(display_name)
        self._set_preview("\n".join(raw_lines))
        self.canvas.draw_idle()
        self.status_var.set(f"Viewing {display_name}")

    def _overlay_comparison_profile(
        self, parameter_name: str, rows: List[List[float]],
        parameters: List[Dict[str, object]], input_path: Path,
    ) -> None:
        """Overlay input B on the existing original and SI plots."""
        widths = [len(row) for row in rows]
        columns = min(widths) if widths else 0
        if columns < 2:
            return
        valid_rows = [row for row in rows if len(row) >= columns]
        is_boundary = parameter_name in {"r_z_psi_bnd_file", "inline_boundary"} and columns >= 3
        coordinate = [row[0] for row in valid_rows]
        x = coordinate if is_boundary else [math.sqrt(value) if value >= 0 else math.nan for value in coordinate]

        for line in self.raw_axes.get_lines():
            label = line.get_label()
            if columns == 2:
                line.set_label("Input A")
                continue
            if label.startswith("_"):
                label = "Boundary" if is_boundary else "Profile"
            if not label.startswith("Input A"):
                line.set_label(f"Input A — {label}")
        if is_boundary:
            self.raw_axes.plot(
                [row[0] for row in valid_rows], [row[1] for row in valid_rows],
                "--", linewidth=1.7, label="Input B — Boundary",
            )
        else:
            for column in range(1, columns):
                self.raw_axes.plot(
                    x, [row[column] for row in valid_rows], "--", linewidth=1.7,
                    label="Input B" if columns == 2 else f"Input B — Column {column + 1}",
                )
        self.raw_axes.legend(loc="best", fontsize="small")

        values = {str(item["name"]).casefold(): str(item["value"]) for item in parameters}
        try:
            mass_number = parse_fortran_float(values["central_mass"])
            number_density_0 = parse_fortran_float(values["central_density"]) * 1e20
            rho_0 = mass_number * number_density_0 * PROTON_MASS_KG
        except (KeyError, ValueError):
            rho_0 = number_density_0 = 0.0

        # In comparison mode, keep density and temperature plots readable by
        # showing only their left-axis quantities (n and T in eV).
        if parameter_name in {"rho_file", "ti_file", "te_file", "t_file"} and self.secondary_axes is not None:
            self.secondary_axes.remove()
            self.secondary_axes = None

        for line in self.axes.get_lines():
            label = line.get_label()
            if label.startswith("_"):
                label = "Psi" if is_boundary else "Profile"
            if (parameter_name in {"ti_file", "te_file", "t_file"}
                    and label.startswith("T") and "(eV)" not in label):
                label = label.replace("T", "T (eV)", 1)
            if not label.startswith("Input A"):
                line.set_label(f"Input A — {label}")
        if self.secondary_axes is not None:
            for line in self.secondary_axes.get_lines():
                label = line.get_label()
                if label.startswith("_"):
                    label = "Profile"
                if (parameter_name in {"ti_file", "te_file", "t_file"}
                        and label.startswith("T") and "(K)" not in label):
                    label = label.replace("T", "T (K)", 1)
                if not label.startswith("Input A"):
                    line.set_label(f"Input A — {label}")

        if is_boundary:
            self.axes.plot(
                range(1, len(valid_rows) + 1), [row[2] for row in valid_rows],
                "--", linewidth=1.7, label="Input B — Psi",
            )
        elif parameter_name == "jsource_file":
            for column in range(1, columns):
                suffix = "" if columns == 2 else f" (column {column + 1})"
                self.axes.plot(x, [row[column] for row in valid_rows], "--", linewidth=1.7,
                               label=f"Input B — J{suffix}")
        elif parameter_name in HEAT_SOURCE_FILE_PARAMETERS and rho_0 > 0:
            multiplier_name = HEAT_SOURCE_FILE_PARAMETERS[parameter_name]
            try:
                multiplier = parse_fortran_float(values[multiplier_name])
            except (KeyError, ValueError):
                multiplier = math.nan
            denominator = ((ADIABATIC_INDEX - 1) * VACUUM_PERMEABILITY
                           * math.sqrt(VACUUM_PERMEABILITY * rho_0))
            for column in range(1, columns):
                converted = [multiplier * row[column] / denominator for row in valid_rows]
                suffix = "" if columns == 2 else f" (column {column + 1})"
                self.axes.plot(x, converted, "--", linewidth=1.7, label=f"Input B — fₛ{suffix}")
        elif parameter_name in {"ti_file", "te_file", "t_file"} and number_density_0 > 0:
            for column in range(1, columns):
                raw = [row[column] for row in valid_rows]
                suffix = "" if columns == 2 else f" (column {column + 1})"
                self.axes.plot(
                    x, [value / (ELEMENTARY_CHARGE * VACUUM_PERMEABILITY * number_density_0) for value in raw],
                    "--", linewidth=1.7, label=f"Input B — T (eV){suffix}",
                )
        elif parameter_name == "rho_file" and rho_0 > 0:
            for column in range(1, columns):
                raw = [row[column] for row in valid_rows]
                suffix = "" if columns == 2 else f" (column {column + 1})"
                self.axes.plot(x, [value * number_density_0 for value in raw], "--", linewidth=1.7,
                               label=f"Input B — n{suffix}")
        elif parameter_name in HEAT_TRANSPORT_FILE_PARAMETERS and rho_0 > 0:
            if self.secondary_axes is None:
                self.secondary_axes = self.axes.twinx()
            factor = math.sqrt(rho_0 / VACUUM_PERMEABILITY) / (ADIABATIC_INDEX - 1)
            density_item = next(
                (item for item in parameters if str(item["name"]).casefold() == "rho_file"), None,
            )
            local_density = None
            if density_item and density_item["file"]:
                density_path = input_path.parent / str(density_item["file"])
                if density_path.is_file():
                    density_rows, _ = read_numeric_file(density_path)
                    density_rows = [row for row in density_rows if len(row) >= 2]
                    if density_rows:
                        normalized = interpolate_linear(
                            [row[0] for row in density_rows], [row[1] for row in density_rows], coordinate,
                        )
                        local_density = [rho_0 * value for value in normalized]
            for column in range(1, columns):
                kappa = [row[column] * factor for row in valid_rows]
                chi = ([value / density if density > 0 else math.nan
                        for value, density in zip(kappa, local_density)]
                       if local_density is not None else [math.nan] * len(kappa))
                suffix = "" if columns == 2 else f" (column {column + 1})"
                self.axes.plot(
                    x, kappa, "--", linewidth=1.7,
                    label=f"Input B — κ (kg m⁻¹ s⁻¹){suffix}", color="#d97706",
                )
                self.secondary_axes.plot(
                    x, chi, ":", linewidth=1.7, color="#d97706",
                    label=f"Input B — χ (m² s⁻¹){suffix}",
                )

        if self.axes.get_lines():
            self.axes.legend(loc="upper left", fontsize="small")
        if self.secondary_axes is not None and self.secondary_axes.get_lines():
            self.secondary_axes.legend(loc="upper right", fontsize="small")

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def apply_plot_xlim(self) -> None:
        """Apply a shared x range and fit each y axis to data inside it."""
        try:
            xmin = float(self.xmin_var.get())
            xmax = float(self.xmax_var.get())
        except ValueError:
            messagebox.showerror("Invalid x limits", "Enter numeric minimum and maximum values.")
            return
        if not math.isfinite(xmin) or not math.isfinite(xmax) or xmin >= xmax:
            messagebox.showerror("Invalid x limits", "The minimum must be smaller than the maximum.")
            return
        axes = [self.raw_axes, self.axes]
        if self.secondary_axes is not None:
            axes.append(self.secondary_axes)
        for axis in axes:
            axis.set_xlim(xmin, xmax)
            self._fit_y_to_xrange(axis, xmin, xmax)
        self.canvas.draw_idle()
        self.status_var.set(f"Plot x limits applied: {xmin:g} to {xmax:g}")

    @staticmethod
    def _fit_y_to_xrange(axis, xmin: float, xmax: float) -> None:
        """Fit an axis's y limits to its finite line data within an x range."""
        visible_y = []  # type: List[float]
        for line in axis.get_lines():
            for x_value, y_value in zip(line.get_xdata(), line.get_ydata()):
                try:
                    x_number = float(x_value)
                    y_number = float(y_value)
                except (TypeError, ValueError):
                    continue
                if xmin <= x_number <= xmax and math.isfinite(y_number):
                    visible_y.append(y_number)
        if not visible_y:
            return
        ymin, ymax = min(visible_y), max(visible_y)
        span = ymax - ymin
        padding = span * 0.05 if span else max(abs(ymin) * 0.05, 1e-12)
        axis.set_ylim(ymin - padding, ymax + padding)

    def reset_plot_limits(self) -> None:
        """Restore automatic x and y limits for every visible plot axis."""
        self.xmin_var.set("")
        self.xmax_var.set("")
        axes = [self.raw_axes, self.axes]
        if self.secondary_axes is not None:
            axes.append(self.secondary_axes)
        for axis in axes:
            axis.set_autoscalex_on(True)
            axis.set_autoscaley_on(True)
            axis.relim()
            axis.autoscale_view()
        self.canvas.draw_idle()
        self.status_var.set("Automatic plot limits restored")

    def _scroll_preview(self, event) -> str:
        """Scroll the complete data preview with the mouse wheel."""
        units = -int(event.delta / 120) if event.delta else 0
        if units:
            self.preview.yview_scroll(units, "units")
        return "break"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="*", type=Path, metavar="INPUT",
        help="zero, one, or two JOREK namelists (default: ./input)",
    )
    args = parser.parse_args()
    if MATPLOTLIB_ERROR:
        raise SystemExit("matplotlib is required. Install it with: python -m pip install matplotlib")
    if len(args.inputs) > 2:
        parser.error("provide at most two input files")
    input_paths = args.inputs or [Path("input")]
    missing = [path for path in input_paths if not path.is_file()]
    if missing:
        raise SystemExit(f"Input file not found: {missing[0]}")
    comparison_path = input_paths[1] if len(input_paths) == 2 else None
    JorekPanel(input_paths[0], comparison_path).mainloop()


if __name__ == "__main__":
    main()
