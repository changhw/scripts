"""Tk-free parsing, conversion, and editing helpers for JOREK inputs."""

import bisect
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
        return "—"
    key = name.casefold()
    if key == "central_density":
        return "{:.8e} m⁻³".format(fj * 1e20)
    if key == "central_mass":
        return "{:.8e} kg".format(fj * proton_mass)
    if key == "i_target":
        return "{:.8e} A".format(fj)
    if key == "particlesource" and fj == 0:
        return "—"
    contextual = ({"eta", "eta_ohmic", "visco", "visco_par", "visco_par_par",
                   "d_perp", "d_par", "particlesource"}
                  | HEAT_TRANSPORT_PARAMETERS | set(HEAT_SOURCE_SCALAR_PARAMETERS))
    if key not in contextual or not values:
        return "—"
    densities = density_constants(values)
    if not densities:
        return "—"
    rho0 = densities[1]
    if key in {"eta", "eta_ohmic"}:
        return "{:.8e} Ω m".format(fj * math.sqrt(mu_0 / rho0))
    if key in {"d_perp", "d_par"}:
        return "{:.8e} m² s⁻¹".format(fj / math.sqrt(mu_0 * rho0))
    if key == "particlesource":
        return "{:.8e} kg s⁻¹ m⁻³".format(fj * math.sqrt(rho0 / mu_0))
    if key in HEAT_SOURCE_SCALAR_PARAMETERS:
        if HEAT_SOURCE_SCALAR_PARAMETERS[key] in values:
            return "—"
        result = fj / ((GAMMA - 1) * mu_0 * math.sqrt(mu_0 * rho0))
        return "{:.8e} W m⁻³".format(result)
    if key in HEAT_TRANSPORT_PARAMETERS:
        coefficient = fj * math.sqrt(rho0 / mu_0) / (GAMMA - 1)
        return "κ={:.8e} kg m⁻¹ s⁻¹; χ={:.8e} m² s⁻¹".format(coefficient, coefficient / rho0)
    dynamic = fj * math.sqrt(rho0 / mu_0)
    return "μ={:.8e} kg m⁻¹ s⁻¹; ν={:.8e} m² s⁻¹".format(dynamic, dynamic / rho0)


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
    return code[:equals + 1] + leading + new_value + trailing + comment + ending


def update_parameter(path, line_number, name, new_value):
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
        os.replace(temporary_name, str(path))
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)
