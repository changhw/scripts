#!/usr/bin/env python3
"""Browser-based JOREK input explorer; requires no Tk or graphical display."""

import argparse
import io
import json
import math
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, quote, urlparse

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from scipy.constants import Boltzmann, elementary_charge, mu_0, proton_mass

from jorek_core import (
    GAMMA, HEAT_SOURCE_FILE_PARAMETERS, HEAT_TRANSPORT_FILE_PARAMETERS,
    canonical_value, density_constants, inline_boundary, interpolate,
    normalization_constants, parameter_map, parse_float, parse_namelist,
    read_numeric_file, update_parameter, value_in_si,
)


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>JOREK Input Explorer</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f5f7fb}body{margin:0}header{background:#172a46;color:white;padding:14px 20px;display:flex;gap:18px;align-items:center}header h1{font-size:20px;margin:0}header span{font-size:12px;opacity:.8}.tabs{display:flex;padding:12px 18px 0;gap:5px}.tabs button{padding:9px 16px;border:0;border-radius:7px 7px 0 0;background:#dce3ef;cursor:pointer}.tabs .active{background:white}.panel{margin:0 18px 18px;background:white;padding:14px;border-radius:0 8px 8px 8px;box-shadow:0 2px 12px #1b31501a}.hidden{display:none!important}#parameters:not(.hidden){height:calc(100vh - 100px);box-sizing:border-box;display:flex;flex-direction:column}#parameters .scroll{flex:1 1 auto;max-height:none;min-height:0}.controls{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex:0 0 auto}.controls input,.controls select{padding:7px;border:1px solid #b8c2d3;border-radius:5px}table{border-collapse:collapse;width:100%;font-size:13px}th{position:sticky;top:0;background:#e8edf5;text-align:left}th,td{padding:6px 8px;border-bottom:1px solid #e4e8ef;vertical-align:top}tr.changed{background:#fff3bf}tr:hover{background:#edf5ff}.scroll{overflow:auto}.profiles{display:grid;grid-template-columns:minmax(260px,28%) 1fr;gap:14px;align-items:stretch}.profiles>div:last-child{display:flex;flex-direction:column;min-height:0}.profile-list button{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid #e2e6ed;background:white;padding:8px;cursor:pointer}.profile-list button:hover,.profile-list button.selected{background:#e8f1ff}.plot{display:block;width:100%;height:auto;background:white;flex:0 0 auto}.plot:not([src]){display:none}.preview{min-height:220px;flex:1 1 auto;overflow:auto;background:#111827;color:#dbeafe;padding:10px;font:12px ui-monospace,monospace;white-space:pre;margin-top:8px;box-sizing:border-box}.edit{border:0;background:#2463a9;color:white;border-radius:4px;padding:4px 8px;cursor:pointer}.note{color:#607089;font-size:12px}</style></head>
<body><header><h1>JOREK Input Explorer</h1><span id="paths"></span></header>
<div class="tabs"><button class="active" data-tab="parameters">Parameters</button><button data-tab="profiles">Referenced profiles</button></div>
<section id="parameters" class="panel"><div class="controls"><label>Filter <input id="filter"></label><span class="note">Changed or missing values are highlighted.</span></div><div class="scroll"><table><thead><tr id="head"></tr></thead><tbody id="rows"></tbody></table></div></section>
<section id="profiles" class="panel hidden"><div class="profiles"><div><h3>Profiles</h3><div class="profile-list" id="profileList"></div></div><div><div class="controls"><label>x min <input id="xmin" size="8"></label><label>x max <input id="xmax" size="8"></label><button id="apply">Apply</button><button id="reset">Reset</button></div><img id="plot" class="plot"><div id="preview" class="preview">Select a profile.</div></div></div></section>
<script>
let state, currentProfile;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.panel').forEach(x=>x.classList.add('hidden'));document.getElementById(b.dataset.tab).classList.remove('hidden')});
async function load(){state=await (await fetch('/api/state')).json(); document.getElementById('paths').textContent=state.paths.join('  |  '); renderTable(); renderProfiles()}
function renderTable(){let cmp=state.compare;document.getElementById('head').innerHTML=['Line','Parameter','JOREK A'].concat(cmp?['JOREK B']:[]).concat(['SI A']).concat(cmp?['SI B']:[]).concat(['Section','']).map(x=>`<th>${x}</th>`).join('');let q=document.getElementById('filter').value.toLowerCase();document.getElementById('rows').innerHTML=state.parameters.filter(r=>JSON.stringify(r).toLowerCase().includes(q)).map(r=>`<tr class="${r.different?'changed':''}"><td>${esc(r.line)}</td><td>${esc(r.name)}</td><td>${esc(r.a)}</td>${cmp?`<td>${esc(r.b)}</td>`:''}<td>${esc(r.si_a)}</td>${cmp?`<td>${esc(r.si_b)}</td>`:''}<td>${esc(r.section)}</td><td>${r.editable?`<button class="edit" onclick="editParam('${esc(r.key)}')">Edit</button>`:''}</td></tr>`).join('')}
document.getElementById('filter').oninput=renderTable;
async function editParam(key){let row=state.parameters.find(r=>r.key===key),side='a';if(state.compare&&row.a!=='—'&&row.b!=='—'){let choice=prompt('Edit which input? Enter A or B. Cancel closes without editing.','A');if(choice===null)return;choice=choice.trim().toLowerCase();if(choice!=='a'&&choice!=='b'){alert('Enter A or B.');return}side=choice}else if(row.a==='—')side='b';let old=side==='a'?row.a:row.b,v=prompt(`New value for ${row.name} in input ${side.toUpperCase()}:`,old);if(v===null)return;let res=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,side,value:v})});let out=await res.json();if(!res.ok){alert(out.error);return}await load()}
function renderProfiles(){document.getElementById('profileList').innerHTML=state.profiles.map(p=>`<button onclick="showProfile('${encodeURIComponent(p.key)}',this)"><b>${esc(p.name)}</b><br><span class="note">${esc(p.files)}</span></button>`).join('')}
async function showProfile(key,button){currentProfile=decodeURIComponent(key);document.querySelectorAll('.profile-list button').forEach(x=>x.classList.remove('selected'));button.classList.add('selected');refreshPlot();let data=await (await fetch('/api/preview?profile='+encodeURIComponent(currentProfile))).json();document.getElementById('preview').textContent=data.text}
function refreshPlot(){if(!currentProfile)return;let p=new URLSearchParams({profile:currentProfile,t:Date.now()});let lo=document.getElementById('xmin').value,hi=document.getElementById('xmax').value;if(lo)p.set('xmin',lo);if(hi)p.set('xmax',hi);document.getElementById('plot').src='/api/plot?'+p}
document.getElementById('apply').onclick=refreshPlot;document.getElementById('reset').onclick=()=>{document.getElementById('xmin').value='';document.getElementById('xmax').value='';refreshPlot()};load();
</script></body></html>"""


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Python 3.6-compatible threaded HTTP server."""

    daemon_threads = True


class BrowserApp(object):
    def __init__(self, first, second=None):
        self.paths = [first.resolve()] + ([second.resolve()] if second else [])
        self.reload()

    def reload(self):
        self.parameters = [parse_namelist(path) for path in self.paths]
        self.values = [parameter_map(items) for items in self.parameters]
        self.profiles = self._profiles()

    def state(self):
        maps = [{str(x["name"]).casefold(): x for x in items} for items in self.parameters]
        names = list(maps[0])
        if len(maps) == 2:
            names += [name for name in maps[1] if name not in maps[0]]
        rows = []
        for key in names:
            a, b = maps[0].get(key), maps[1].get(key) if len(maps) == 2 else None
            item = a or b
            av, bv = (str(a["value"]) if a else "—"), (str(b["value"]) if b else "—")
            different = len(maps) == 2 and (not a or not b or canonical_value(av) != canonical_value(bv))
            rows.append({"key": key, "name": item["name"], "line": a["line"] if a else b["line"],
                         "a": av, "b": bv, "si_a": value_in_si(item["name"], av, self.values[0]) if a else "—",
                         "si_b": value_in_si(item["name"], bv, self.values[1]) if b else "—",
                         "section": item["section"], "different": different, "editable": True})
        for index, (name, unit) in enumerate((("v_JOREK", "m s⁻¹"), ("t_JOREK", "ms"))):
            constants = [normalization_constants(v) for v in self.values]
            a = "{:.8e} {}".format(constants[0][index], unit) if constants[0] else "—"
            b = "{:.8e} {}".format(constants[1][index], unit) if len(constants) == 2 and constants[1] else "—"
            rows.insert(index, {"key": name.casefold(), "name": name, "line": "—", "a": "1 unit", "b": "1 unit" if len(constants)==2 else "—", "si_a": a, "si_b": b, "section": "Derived constants", "different": len(constants)==2 and a!=b, "editable": False})
        return {"paths": [str(p) for p in self.paths], "compare": len(self.paths) == 2,
                "parameters": rows, "profiles": [{"key": k, "name": v["name"], "files": v["files"]} for k,v in self.profiles.items()]}

    def _profiles(self):
        result = {}
        for side, items in enumerate(self.parameters):
            for item in items:
                if not item["file"]:
                    continue
                key = str(item["name"]).casefold()
                source = {"path": self.paths[side].parent / str(item["file"]), "parameter": key}
                entry = result.setdefault(key, {"name": item["name"], "sources": [None] * len(self.paths)})
                entry["sources"][side] = source
            boundary = inline_boundary(items)
            if boundary:
                entry = result.setdefault("boundary", {"name": "R/Z/Psi boundary", "sources": [None] * len(self.paths)})
                entry["sources"][side] = {"rows": boundary, "parameter": "boundary"}
        # Pair file-backed and inline boundaries under one entry.
        file_boundary = result.pop("r_z_psi_bnd_file", None)
        if file_boundary:
            entry = result.setdefault("boundary", {"name": "R/Z/Psi boundary", "sources": [None] * len(self.paths)})
            for i, source in enumerate(file_boundary["sources"]):
                if source:
                    entry["sources"][i] = source
        for entry in result.values():
            labels = []
            for i, source in enumerate(entry["sources"]):
                if source:
                    labels.append("{}: {}".format("AB"[i], source.get("path", "inline lists")))
            entry["files"] = " | ".join(labels)
        return result

    def source_rows(self, source):
        if "rows" in source:
            rows = source["rows"]
            lines = ["R_boundary Z_boundary Psi_boundary"] + ["{:.12e} {:.12e} {:.12e}".format(*r) for r in rows]
            return rows, lines
        if not source["path"].is_file():
            return [], ["Missing referenced file: {}".format(source["path"])]
        return read_numeric_file(source["path"])

    def edit(self, key, side, value):
        index = 0 if side == "a" else 1
        if index >= len(self.parameters):
            raise ValueError("Input side is unavailable")
        item = next((x for x in self.parameters[index] if str(x["name"]).casefold() == key), None)
        if not item:
            raise ValueError("Parameter is not present in that input")
        update_parameter(self.paths[index], int(item["line"]), str(item["name"]), value.strip())
        self.reload()

    def converted(self, key, rows, side):
        valid = [row for row in rows if len(row) >= 2]
        coordinate = [row[0] for row in valid]
        raw = [row[1] for row in valid]
        values, densities = self.values[side], density_constants(self.values[side])
        if key == "jsource_file": return raw, "Current density (A m⁻²)", None, None
        if key == "rho_file" and densities:
            return ([v * densities[0] for v in raw], "n (m⁻³)",
                    [v * densities[1] for v in raw], "ρ (kg m⁻³)")
        if key in {"ti_file", "te_file", "t_file"} and densities:
            return ([v / (elementary_charge * mu_0 * densities[0]) for v in raw], "T (eV)",
                    [v / (Boltzmann * mu_0 * densities[0]) for v in raw], "T (K)")
        if key in HEAT_SOURCE_FILE_PARAMETERS and densities:
            multiplier = parse_float(values[HEAT_SOURCE_FILE_PARAMETERS[key]])
            factor = multiplier / ((GAMMA - 1) * mu_0 * math.sqrt(mu_0 * densities[1]))
            return [v * factor for v in raw], "Heat source (W m⁻³)", None, None
        if key in HEAT_TRANSPORT_FILE_PARAMETERS and densities:
            factor = math.sqrt(densities[1] / mu_0) / (GAMMA - 1)
            kappa = [v * factor for v in raw]
            density_item = next(
                (item for item in self.parameters[side]
                 if str(item["name"]).casefold() == "rho_file" and item["file"]), None,
            )
            chi = None
            if density_item:
                density_path = self.paths[side].parent / str(density_item["file"])
                if density_path.is_file():
                    density_rows, _ = read_numeric_file(density_path)
                    density_rows = [row for row in density_rows if len(row) >= 2]
                    if density_rows:
                        normalized_density = interpolate(
                            [row[0] for row in density_rows], [row[1] for row in density_rows], coordinate,
                        )
                        local_density = [densities[1] * value for value in normalized_density]
                        chi = [value / density if density > 0 else math.nan
                               for value, density in zip(kappa, local_density)]
            return kappa, "κ (kg m⁻¹ s⁻¹)", chi, "χ (m² s⁻¹)" if chi is not None else None
        return None, "No SI conversion configured", None, None

    def plot(self, key, xmin=None, xmax=None):
        entry = self.profiles[key]
        fig = Figure(figsize=(11, 4.5), dpi=120)
        original, converted = fig.subplots(1, 2)
        converted_secondary = None
        colors = ("#1769aa", "#d97706")
        for side, source in enumerate(entry["sources"]):
            if not source:
                continue
            rows, _ = self.source_rows(source)
            columns = min([len(r) for r in rows]) if rows else 0
            if not rows:
                original.text(.5, .5 - side * .08, "Input {}: missing or unreadable profile".format("AB"[side]),
                              ha="center", va="center", transform=original.transAxes)
                continue
            boundary = key == "boundary" and columns >= 3
            valid = [r for r in rows if len(r) >= columns]
            label, style = "Input {}".format("AB"[side]), "-" if side == 0 else "--"
            if boundary:
                original.plot([r[0] for r in valid], [r[1] for r in valid], style, color=colors[side], label=label)
                converted.plot(range(1, len(valid)+1), [r[2] for r in valid], style, color=colors[side], label=label)
                original.set(xlabel="R", ylabel="Z", title="Boundary shape"); original.set_aspect("equal", adjustable="datalim")
                converted.set(xlabel="Row ID", ylabel="Psi", title="Psi by boundary row")
            elif columns >= 2:
                psi = [r[0] for r in valid]
                x = [math.sqrt(v) if v >= 0 else math.nan for v in psi]
                original.plot(x, [r[1] for r in valid], style, color=colors[side], label=label)
                y, ylabel, secondary_y, secondary_ylabel = self.converted(key, valid, side)
                if y is not None:
                    primary_label = (
                        label + " — " + ylabel if key in HEAT_TRANSPORT_FILE_PARAMETERS else label
                    )
                    converted.plot(x, y, style, color=colors[side], label=primary_label)
                    converted.set_ylabel(ylabel)
                # Density and temperature comparisons intentionally show only
                # their left-axis quantities; single-input plots show both.
                show_secondary = not (len(self.paths) == 2 and key in {"rho_file", "ti_file", "te_file", "t_file"})
                if secondary_y is not None and show_secondary:
                    if converted_secondary is None:
                        converted_secondary = converted.twinx()
                    converted_secondary.plot(
                        x, secondary_y, ":" if side else "--", color=colors[side],
                        label=label + " — " + secondary_ylabel,
                    )
                    converted_secondary.set_ylabel(secondary_ylabel)
                original.set(xlabel=r"$\sqrt{\psi_n}$", ylabel="JOREK value", title="Original profile")
                converted.set(xlabel=r"$\sqrt{\psi_n}$", title="SI profile")
        for axis in (original, converted):
            axis.grid(True, alpha=.25)
            if axis.lines:
                axis.legend()
            if xmin is not None and xmax is not None:
                axis.set_xlim(xmin, xmax)
                visible = [float(y) for line in axis.lines for x,y in zip(line.get_xdata(),line.get_ydata()) if xmin <= float(x) <= xmax and math.isfinite(float(y))]
                if visible:
                    lo, hi = min(visible), max(visible); pad = (hi-lo)*.05 if hi!=lo else max(abs(lo)*.05,1e-12); axis.set_ylim(lo-pad,hi+pad)
        if converted_secondary is not None:
            converted_secondary.legend(loc="upper right")
            if xmin is not None and xmax is not None:
                converted_secondary.set_xlim(xmin, xmax)
                visible = [float(y) for line in converted_secondary.lines for x,y in zip(line.get_xdata(),line.get_ydata()) if xmin <= float(x) <= xmax and math.isfinite(float(y))]
                if visible:
                    lo, hi = min(visible), max(visible); pad = (hi-lo)*.05 if hi!=lo else max(abs(lo)*.05,1e-12); converted_secondary.set_ylim(lo-pad,hi+pad)
        fig.tight_layout()
        output = io.BytesIO(); FigureCanvasAgg(fig).print_png(output); return output.getvalue()


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, content, content_type):
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(content))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(content)
        def do_GET(self):
            parsed = urlparse(self.path); query = parse_qs(parsed.query)
            try:
                if parsed.path == "/": return self.send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                if parsed.path == "/api/state": return self.send(200, json.dumps(app.state(), ensure_ascii=False).encode("utf-8"), "application/json")
                if parsed.path == "/api/preview":
                    entry=app.profiles[query["profile"][0]]; chunks=[]
                    for i,source in enumerate(entry["sources"]):
                        if source: chunks.append("===== INPUT {} =====\n{}".format("AB"[i],"\n".join(app.source_rows(source)[1])))
                    return self.send(200,json.dumps({"text":"\n\n".join(chunks)},ensure_ascii=False).encode("utf-8"),"application/json")
                if parsed.path == "/api/plot":
                    lo=query.get("xmin",[None])[0]; hi=query.get("xmax",[None])[0]
                    data=app.plot(query["profile"][0],float(lo) if lo else None,float(hi) if hi else None)
                    return self.send(200,data,"image/png")
                self.send(404,b"Not found","text/plain")
            except Exception as exc:
                self.send(500,str(exc).encode("utf-8"),"text/plain")
        def do_POST(self):
            try:
                length=int(self.headers.get("Content-Length","0")); data=json.loads(self.rfile.read(length).decode("utf-8"))
                if urlparse(self.path).path != "/api/edit": return self.send(404,b"{}","application/json")
                app.edit(data["key"],data["side"],data["value"])
                self.send(200,b'{"ok":true}',"application/json")
            except Exception as exc:
                self.send(400,json.dumps({"error":str(exc)}).encode("utf-8"),"application/json")
        def log_message(self, fmt, *args):
            print("[jorek-web] " + fmt % args)
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs",nargs="*",type=Path,metavar="INPUT")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--open-browser",action="store_true")
    args=parser.parse_args()
    if len(args.inputs)>2: parser.error("provide at most two inputs")
    paths=args.inputs or [Path("input")]
    for path in paths:
        if not path.is_file(): parser.error("input not found: {}".format(path))
    app=BrowserApp(paths[0],paths[1] if len(paths)==2 else None)
    server=ThreadingHTTPServer((args.host,args.port),make_handler(app))
    url="http://{}:{}/".format(args.host,args.port)
    print("JOREK browser panel: {}".format(url))
    if args.host in {"127.0.0.1","localhost"}: print("Remote access: ssh -L {0}:127.0.0.1:{0} user@server".format(args.port))
    if args.open_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
