#!/usr/bin/env python3
"""Browser-based JOREK input, processing, and visualization panel."""

import argparse
import io
import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from scipy.constants import Boltzmann, elementary_charge, mu_0, proton_mass

from jorek_core import (
    GAMMA, HEAT_SOURCE_FILE_PARAMETERS, HEAT_TRANSPORT_FILE_PARAMETERS,
    canonical_value, density_constants, inline_boundary, interpolate,
    format_operation_command, format_plot_command, jorek_operation_command,
    jorek_plot_command, normalization_constants, operation_definitions,
    operation_environment, parameter_map, parse_float, parse_namelist,
    path_completions, plot_definitions,
    read_numeric_file, update_parameter, value_in_si,
)

PLOT_X_PSI = r"$\sqrt{\psi_n}$"
PLOT_R = r"$R$"
PLOT_Z = r"$Z$"
PLOT_PSI = r"$\Psi$"
PLOT_CURRENT_DENSITY = r"$J\;(\mathrm{A\,m^{-2}})$"
PLOT_HEAT_SOURCE = r"$f_s\;(\mathrm{W\,m^{-3}})$"
PLOT_T_EV = r"$T\;(\mathrm{eV})$"
PLOT_T_K = r"$T\;(\mathrm{K})$"
PLOT_NUMBER_DENSITY = r"$n\;(\mathrm{m^{-3}})$"
PLOT_MASS_DENSITY = r"$\rho\;(\mathrm{kg\,m^{-3}})$"
PLOT_KAPPA = r"$\kappa\;(\mathrm{kg\,m^{-1}\,s^{-1}})$"
PLOT_CHI = r"$\chi\;(\mathrm{m^2\,s^{-1}})$"


HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>MHD Control Panel</title>
<style>
:root{font-family:system-ui,sans-serif;color:#172033;background:#f5f7fb}body{margin:0}header{background:#172a46;color:white;padding:14px 20px;display:flex;gap:18px;align-items:center}header h1{font-size:20px;margin:0}header span{font-size:12px;opacity:.8}.tabs{display:flex;padding:12px 18px 0;gap:5px}.tabs button{padding:9px 16px;border:0;border-radius:7px 7px 0 0;background:#dce3ef;cursor:pointer}.tabs .active{background:white}.panel{margin:0 18px 18px;background:white;padding:14px;border-radius:0 8px 8px 8px;box-shadow:0 2px 12px #1b31501a}.hidden{display:none!important}#parameters:not(.hidden){height:calc(100vh - 100px);box-sizing:border-box;display:flex;flex-direction:column}#parameters .scroll{flex:1 1 auto;max-height:none;min-height:0}.controls{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex:0 0 auto}.controls input,.controls select{padding:7px;border:1px solid #b8c2d3;border-radius:5px}table{border-collapse:collapse;width:100%;font-size:13px}th{position:sticky;top:0;background:#e8edf5;text-align:left}th,td{padding:6px 8px;border-bottom:1px solid #e4e8ef;vertical-align:top}tr.changed{background:#fff3bf}tr:hover{background:#edf5ff}.scroll{overflow:auto}.profiles{display:grid;grid-template-columns:minmax(260px,28%) 1fr;gap:14px;align-items:stretch}.profiles>div:last-child{display:flex;flex-direction:column;min-height:0}.profile-list button{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid #e2e6ed;background:white;padding:8px;cursor:pointer}.profile-list button:hover,.profile-list button.selected{background:#e8f1ff}.plot{display:block;width:100%;height:auto;background:white;flex:0 0 auto}.plot:not([src]){display:none}.preview{min-height:220px;flex:1 1 auto;overflow:auto;background:#111827;color:#dbeafe;padding:10px;font:12px ui-monospace,monospace;white-space:pre;margin-top:8px;box-sizing:border-box}.edit,.run{border:0;background:#2463a9;color:white;border-radius:4px;padding:6px 10px;cursor:pointer}.stop{border:0;background:#b42318;color:white;border-radius:4px;padding:6px 10px;cursor:pointer}.edit:disabled,.run:disabled,.stop:disabled{opacity:.45;cursor:default}.note{color:#607089;font-size:12px}.operation-grid{display:grid;grid-template-columns:180px minmax(260px,520px) 1fr;gap:8px;align-items:center}.operation-grid input,.operation-grid select{padding:7px;border:1px solid #b8c2d3;border-radius:5px}.command{font:13px ui-monospace,monospace;background:#eef2f7;padding:9px;border-radius:5px;margin:10px 0}.operation-output{height:calc(100vh - 390px);min-height:260px}.viz-layout{display:grid;grid-template-columns:minmax(360px,42%) 1fr;gap:14px}.viz-image{display:block;max-width:100%;max-height:58vh;margin:auto}.viz-image:not([src]){display:none}.viz-log{min-height:100px;max-height:180px}</style></head>
<body><header><h1>MHD Control Panel</h1><span id="paths"></span></header>
<div class="tabs"><button class="active" data-tab="parameters">Parameters</button><button data-tab="profiles">Referenced profiles</button><button data-tab="operations">Convert / post-process</button><button data-tab="visualization">Visualize results</button></div>
<section id="parameters" class="panel"><div class="controls"><label>Filter <input id="filter"></label><span class="note">Changed or missing values are highlighted.</span></div><div class="scroll"><table><thead><tr id="head"></tr></thead><tbody id="rows"></tbody></table></div></section>
<section id="profiles" class="panel hidden"><div class="profiles"><div><h3>Profiles</h3><div class="profile-list" id="profileList"></div></div><div><div class="controls"><label>x min <input id="xmin" size="8"></label><label>x max <input id="xmax" size="8"></label><button id="apply">Apply</button><button id="reset">Reset</button></div><img id="plot" class="plot"><div id="preview" class="preview">Select a profile.</div></div></div></section>
<section id="operations" class="panel hidden"><div class="operation-grid"><label for="operationSelect">Operation</label><select id="operationSelect"></select><span id="operationNote" class="note"></span><span>Working directory</span><code id="operationCwd"></code><span class="note">The directory containing input A.</span></div><div id="operationFields" class="operation-grid" style="margin-top:12px"></div><div id="commandPreview" class="command"></div><div class="controls"><button id="runOperation" class="run">Run</button><button id="stopOperation" class="stop">Stop</button><button id="clearOperation">Clear output</button><span id="operationStatus" class="note">Idle</span></div><pre id="operationOutput" class="preview operation-output"></pre></section>
<section id="visualization" class="panel hidden"><div class="viz-layout"><div><div class="operation-grid"><label for="vizSelect">Plot utility</label><select id="vizSelect"></select><span id="vizNote" class="note"></span><span>Working directory</span><code id="vizCwd"></code><span class="note">The directory containing input A.</span></div><div id="vizFields" class="operation-grid" style="margin-top:12px"></div><div id="vizPreview" class="command"></div><div class="controls"><button id="runViz" class="run">Generate</button><button id="stopViz" class="stop">Stop</button><span id="vizStatus" class="note">Idle</span></div><pre id="vizOutput" class="preview viz-log"></pre></div><div><div class="controls"><button id="previousViz">Previous</button><button id="nextViz">Next</button><button id="standaloneViz">Open standalone</button><button id="downloadViz">Export .mplfig</button><span id="vizImageStatus" class="note">No captured figure</span></div><img id="vizImage" class="viz-image"></div></div></section>
<script>
let state, currentProfile, operationTimer, vizTimer, vizIndex=0, vizCount=0, vizFigureCount=0, vizFigureAvailable=[];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelectorAll('.panel').forEach(x=>x.classList.add('hidden'));document.getElementById(b.dataset.tab).classList.remove('hidden')});
async function load(){state=await (await fetch('/api/state')).json();document.getElementById('paths').textContent=state.paths.join('  |  ');document.getElementById('operationCwd').textContent=state.operation_directory;document.getElementById('vizCwd').textContent=state.operation_directory;if(currentProfile&&!state.profiles.some(p=>p.key===currentProfile)){currentProfile=null;document.getElementById('plot').removeAttribute('src');document.getElementById('preview').textContent='Select a profile.'}renderTable();renderProfiles();renderOperationSelector();renderVizSelector();if(currentProfile){refreshPlot();refreshPreview()}pollOperation();pollViz()}
function renderTable(){let cmp=state.compare;document.getElementById('head').innerHTML=['Line','Parameter','JOREK A'].concat(cmp?['JOREK B']:[]).concat(['SI A']).concat(cmp?['SI B']:[]).concat(['Section','']).map(x=>`<th>${x}</th>`).join('');let q=document.getElementById('filter').value.toLowerCase();document.getElementById('rows').innerHTML=state.parameters.filter(r=>[r.line,r.name,r.a,r.b,r.si_a,r.si_b,r.section].join(' ').toLowerCase().includes(q)).map(r=>`<tr class="${r.different?'changed':''}"><td>${esc(r.line)}</td><td>${esc(r.name)}</td><td>${esc(r.a)}</td>${cmp?`<td>${esc(r.b)}</td>`:''}<td>${esc(r.si_a)}</td>${cmp?`<td>${esc(r.si_b)}</td>`:''}<td>${esc(r.section)}</td><td>${r.editable?`<button class="edit" onclick="editParam('${esc(r.key)}')">Edit</button>`:''}</td></tr>`).join('')}
document.getElementById('filter').oninput=renderTable;
async function editParam(key){let row=state.parameters.find(r=>r.key===key),side='a';if(state.compare&&row.a!=='--'&&row.b!=='--'){let choice=prompt('Edit which input? Enter A or B. Cancel closes without editing.','A');if(choice===null)return;choice=choice.trim().toLowerCase();if(choice!=='a'&&choice!=='b'){alert('Enter A or B.');return}side=choice}else if(row.a==='--')side='b';let old=side==='a'?row.a:row.b,v=prompt(`New value for ${row.name} in input ${side.toUpperCase()}:`,old);if(v===null)return;let res=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,side,value:v})});let out=await res.json();if(!res.ok){alert(out.error);return}await load()}
function renderProfiles(){document.getElementById('profileList').innerHTML=state.profiles.map(p=>`<button onclick="showProfile('${encodeURIComponent(p.key)}',this)"><b>${esc(p.name)}</b><br><span class="note">${esc(p.files)}</span></button>`).join('');if(currentProfile){let i=state.profiles.findIndex(p=>p.key===currentProfile),b=document.querySelectorAll('.profile-list button')[i];if(b)b.classList.add('selected')}}
async function showProfile(key,button){currentProfile=decodeURIComponent(key);document.getElementById('xmin').value='';document.getElementById('xmax').value='';document.querySelectorAll('.profile-list button').forEach(x=>x.classList.remove('selected'));button.classList.add('selected');refreshPlot();refreshPreview()}
async function refreshPreview(){if(!currentProfile)return;let data=await (await fetch('/api/preview?profile='+encodeURIComponent(currentProfile))).json();document.getElementById('preview').textContent=data.text}
function refreshPlot(){if(!currentProfile)return;let p=new URLSearchParams({profile:currentProfile,t:Date.now()});let lo=document.getElementById('xmin').value.trim(),hi=document.getElementById('xmax').value.trim();if(lo&&hi){p.set('xmin',lo);p.set('xmax',hi)}document.getElementById('plot').src='/api/plot?'+p}
document.getElementById('apply').onclick=()=>{let lo=document.getElementById('xmin').value.trim(),hi=document.getElementById('xmax').value.trim();if(!lo&&!hi){refreshPlot();return}if(!lo||!hi){alert('Fill in both x limits, or clear both boxes for automatic limits.');return}let a=Number(lo),b=Number(hi);if(!isFinite(a)||!isFinite(b)||a>=b){alert('Enter numeric x limits with the minimum smaller than the maximum.');return}refreshPlot()};document.getElementById('reset').onclick=()=>{document.getElementById('xmin').value='';document.getElementById('xmax').value='';refreshPlot()};
async function refreshPathSuggestions(input,scope,field){let serial=String((Number(input.dataset.requestSerial)||0)+1);input.dataset.requestSerial=serial;let query=new URLSearchParams({scope:scope,field:field,value:input.value}),response=await fetch('/api/autocomplete?'+query),matches=await response.json();if(input.dataset.requestSerial!==serial)return;let list=document.getElementById(input.getAttribute('list'));if(list)list.innerHTML=matches.map(value=>`<option value="${esc(value)}"></option>`).join('')}
function renderOperationSelector(){let s=document.getElementById('operationSelect'),selected=s.value||state.operations[0].name;s.innerHTML=state.operations.map(o=>`<option value="${esc(o.name)}">${esc(o.label)} (${esc(o.name)})</option>`).join('');s.value=state.operations.some(o=>o.name===selected)?selected:state.operations[0].name;renderOperationFields()}
function renderOperationFields(){let op=state.operations.find(o=>o.name===document.getElementById('operationSelect').value);document.getElementById('operationNote').textContent=op.group;document.getElementById('operationFields').innerHTML=op.fields.map(f=>{let id='op_'+f.name,list=f.path_kind?` list="${esc(id)}_paths" data-path-kind="${esc(f.path_kind)}"`:'';return `<label for="${esc(id)}">${esc(f.label)}</label><input id="${esc(id)}" data-field="${esc(f.name)}"${list} value="${esc(f.name==='input'?state.input_name:f.default||'')}">${f.path_kind?`<datalist id="${esc(id)}_paths"></datalist>`:''}<span class="note">${esc(f.help||'')}</span>`}).join('');document.querySelectorAll('#operationFields input').forEach(x=>{x.oninput=()=>{updateOperationPreview();if(x.dataset.pathKind)refreshPathSuggestions(x,'operation',x.dataset.field)};x.onfocus=()=>{if(x.dataset.pathKind)refreshPathSuggestions(x,'operation',x.dataset.field)}});updateOperationPreview()}
function operationValues(){let v={};document.querySelectorAll('#operationFields input').forEach(x=>v[x.dataset.field]=x.value);return v}
function updateOperationPreview(){let name=document.getElementById('operationSelect').value,values=operationValues(),assignments=[],args=[];Object.keys(values).forEach(key=>{let value=values[key].trim();if(!value)return;if(key==='omp_threads')assignments.push('OMP_NUM_THREADS='+value);else if(key==='control_file')args.push('-fn',value);else args.push(value)});document.getElementById('commandPreview').textContent='$ '+(assignments.length?assignments.join(' ')+' ':'')+name+(args.length?' '+args.join(' '):'')}
document.getElementById('operationSelect').onchange=renderOperationFields;
document.getElementById('runOperation').onclick=async()=>{let res=await fetch('/api/operation/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:document.getElementById('operationSelect').value,values:operationValues()})}),out=await res.json();if(!res.ok){alert(out.error);return}pollOperation()};
document.getElementById('stopOperation').onclick=async()=>{let res=await fetch('/api/operation/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),out=await res.json();if(!res.ok)alert(out.error);pollOperation()};
document.getElementById('clearOperation').onclick=async()=>{await fetch('/api/operation/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});pollOperation()};
async function pollOperation(){clearTimeout(operationTimer);let job=await (await fetch('/api/operation')).json(),output=document.getElementById('operationOutput');output.textContent=job.log||'';output.scrollTop=output.scrollHeight;document.getElementById('operationStatus').textContent=job.status+(job.exit_code===null?'':' (status '+job.exit_code+')');let running=job.status==='running'||job.status==='stopping';document.getElementById('runOperation').disabled=running;document.getElementById('stopOperation').disabled=!running;if(running)operationTimer=setTimeout(pollOperation,1000)}
function renderVizSelector(){let s=document.getElementById('vizSelect'),selected=s.value||(state.plots[0]||{}).name,available=state.plots.find(p=>p.available);s.innerHTML=state.plots.map(p=>`<option value="${esc(p.name)}" ${p.available?'':'disabled'}>${esc(p.label)} (${esc(p.script)})${p.available?'':' -- unavailable'}</option>`).join('');if(!available){s.value='';document.getElementById('vizNote').textContent='No plotting utilities were found';document.getElementById('vizFields').innerHTML='';document.getElementById('vizPreview').textContent='';document.getElementById('runViz').disabled=true;return}s.value=state.plots.some(p=>p.name===selected&&p.available)?selected:available.name;renderVizFields()}
function renderVizFields(){let p=state.plots.find(x=>x.name===document.getElementById('vizSelect').value);if(!p){document.getElementById('runViz').disabled=true;return}document.getElementById('vizNote').textContent=p.available?p.script_path:'Script not found';document.getElementById('vizFields').innerHTML=p.fields.map(f=>{let id='viz_'+f.name,choices=f.boolean?['true','false']:(f.choices||null),list=f.path_kind?` list="${esc(id)}_paths" data-path-kind="${esc(f.path_kind)}"`:'',control=choices?`<select id="${esc(id)}" data-field="${esc(f.name)}">${choices.map(x=>`<option ${x===f.default?'selected':''}>${esc(x)}</option>`).join('')}</select>`:`<input id="${esc(id)}" data-field="${esc(f.name)}"${list} value="${esc(f.default||'')}">${f.path_kind?`<datalist id="${esc(id)}_paths"></datalist>`:''}`;return `<label for="${esc(id)}">${esc(f.label)}</label>${control}<span class="note">${esc(f.help||'')}</span>`}).join('');document.querySelectorAll('#vizFields input,#vizFields select').forEach(x=>{x.oninput=()=>{updateVizPreview();if(x.dataset.pathKind)refreshPathSuggestions(x,'plot',x.dataset.field)};x.onfocus=()=>{if(x.dataset.pathKind)refreshPathSuggestions(x,'plot',x.dataset.field)}});document.getElementById('runViz').disabled=!p.available;updateVizPreview()}
function vizValues(){let v={};document.querySelectorAll('#vizFields [data-field]').forEach(x=>v[x.dataset.field]=x.value);return v}
function updateVizPreview(){let p=state.plots.find(x=>x.name===document.getElementById('vizSelect').value),values=vizValues(),args=[];p.fields.forEach(f=>{let value=(values[f.name]||'').trim();if(!value)return;if(f.name.toLowerCase().includes('multiplier')&&['$time2si','time2si','$t_jorek','t_jorek'].includes(value.toLowerCase())&&state.time2si!==null)value=String(state.time2si);if(f.name==='extra_args'&&state.time2si!==null)value=value.replace(/\$(?:time2si|t_jorek)\b/gi,String(state.time2si));if(f.boolean){let yes=['1','true','yes','on'].includes(value.toLowerCase());if(f.boolean==='flag'){if(yes)args.push(f.flag)}else if(f.boolean==='either')args.push(yes?f.flag:f.false_flag);else args.push(f.flag,yes?'true':'false')}else{let flag=(p.name==='plot_live_data'&&f.name==='title')?'-title':(p.name==='plot_q_versus_time'&&f.name==='time_multiplier')?'-xm':f.flag;if(flag)args.push(flag);args.push(value)}});document.getElementById('vizPreview').textContent='$ '+p.script+(args.length?' '+args.join(' '):'')}
document.getElementById('vizSelect').onchange=renderVizFields;
document.getElementById('runViz').onclick=async()=>{let res=await fetch('/api/visualization/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plot:document.getElementById('vizSelect').value,values:vizValues()})}),out=await res.json();if(!res.ok){alert(out.error);return}vizIndex=0;pollViz()};
document.getElementById('stopViz').onclick=async()=>{let res=await fetch('/api/visualization/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),out=await res.json();if(!res.ok)alert(out.error);pollViz()};
function showViz(){let image=document.getElementById('vizImage');if(!vizCount){image.removeAttribute('src');document.getElementById('vizImageStatus').textContent='No captured figure';document.getElementById('downloadViz').disabled=true;document.getElementById('standaloneViz').disabled=true;return}image.src='/api/visualization/image?index='+vizIndex+'&t='+Date.now();document.getElementById('vizImageStatus').textContent='Figure '+(vizIndex+1)+' of '+vizCount;document.getElementById('previousViz').disabled=document.getElementById('nextViz').disabled=vizCount<2;document.getElementById('downloadViz').disabled=!vizFigureAvailable[vizIndex];document.getElementById('standaloneViz').disabled=false}
document.getElementById('previousViz').onclick=()=>{if(vizCount){vizIndex=(vizIndex-1+vizCount)%vizCount;showViz()}};
document.getElementById('nextViz').onclick=()=>{if(vizCount){vizIndex=(vizIndex+1)%vizCount;showViz()}};
document.getElementById('downloadViz').onclick=()=>{if(vizFigureAvailable[vizIndex])window.location='/api/visualization/figure?index='+vizIndex};
document.getElementById('standaloneViz').onclick=()=>{if(vizCount)window.open('/api/visualization/image?index='+vizIndex+'&t='+Date.now(),'mhd-figure-'+vizIndex,'width=1100,height=800,resizable=yes,scrollbars=yes')};
async function pollViz(){clearTimeout(vizTimer);let job=await (await fetch('/api/visualization')).json(),output=document.getElementById('vizOutput');output.textContent=job.log||'';output.scrollTop=output.scrollHeight;document.getElementById('vizStatus').textContent=job.status+(job.exit_code===null?'':' (status '+job.exit_code+')');let running=job.status==='running'||job.status==='stopping',selected=state.plots.find(p=>p.name===document.getElementById('vizSelect').value),available=!!(selected&&selected.available);document.getElementById('runViz').disabled=running||!available;document.getElementById('stopViz').disabled=!running;vizFigureCount=job.figure_count||0;vizFigureAvailable=job.figure_available||[];if(job.image_count!==vizCount){vizCount=job.image_count;vizIndex=0;showViz()}else showViz();if(running)vizTimer=setTimeout(pollViz,1000)}
load();
</script></body></html>"""


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Python 3.6-compatible threaded HTTP server."""

    daemon_threads = True


class BrowserApp(object):
    def __init__(self, first, second=None):
        self.paths = [first.resolve()] + ([second.resolve()] if second else [])
        self.data_lock = threading.RLock()
        self.shutting_down = False
        self.operation_lock = threading.Lock()
        self.operation_process = None
        self.operation_thread = None
        self.operation_job = {
            "status": "idle", "operation": None, "exit_code": None, "log": "",
        }
        self.visualization_lock = threading.Lock()
        self.visualization_process = None
        self.visualization_thread = None
        self.visualization_directory = None
        self.visualization_previous_directory = None
        self.visualization_images = []
        self.visualization_figures = []
        self.visualization_job = {
            "status": "idle", "plot": None, "exit_code": None, "log": "",
            "image_count": 0, "figure_count": 0, "figure_available": [],
        }
        self.reload()

    def reload(self):
        with self.data_lock:
            parameters = [parse_namelist(path) for path in self.paths]
            self.parameters = parameters
            self.values = [parameter_map(items) for items in parameters]
            self.profiles = self._profiles()

    def state(self):
        with self.data_lock:
            return self._state()

    def _state(self):
        maps = [{str(x["name"]).casefold(): x for x in items} for items in self.parameters]
        names = list(maps[0])
        if len(maps) == 2:
            names += [name for name in maps[1] if name not in maps[0]]
        rows = []
        for key in names:
            a, b = maps[0].get(key), maps[1].get(key) if len(maps) == 2 else None
            item = a or b
            av, bv = (str(a["value"]) if a else "--"), (str(b["value"]) if b else "--")
            different = len(maps) == 2 and (not a or not b or canonical_value(av) != canonical_value(bv))
            rows.append({"key": key, "name": item["name"], "line": a["line"] if a else b["line"],
                         "a": av, "b": bv, "si_a": value_in_si(item["name"], av, self.values[0]) if a else "--",
                         "si_b": value_in_si(item["name"], bv, self.values[1]) if b else "--",
                         "section": item["section"], "different": different, "editable": True})
        constants = [normalization_constants(v) for v in self.values]
        if any(constants):
            for index, (name, unit) in enumerate((("v_JOREK", "m s^-1"), ("t_JOREK", "ms"))):
                a = "{:.8e} {}".format(constants[0][index], unit) if constants[0] else "--"
                b = "{:.8e} {}".format(constants[1][index], unit) if len(constants) == 2 and constants[1] else "--"
                rows.insert(index, {"key": name.casefold(), "name": name, "line": "--", "a": "1 unit", "b": "1 unit" if len(constants)==2 else "--", "si_a": a, "si_b": b, "section": "Derived constants", "different": len(constants)==2 and a!=b, "editable": False})
        return {"paths": [str(p) for p in self.paths], "compare": len(self.paths) == 2,
                "parameters": rows,
                "profiles": [{"key": k, "name": v["name"], "files": v["files"]}
                             for k, v in self.profiles.items()],
                "operations": operation_definitions(),
                "plots": plot_definitions(),
                "operation_directory": str(self.paths[0].parent),
                "input_name": self.paths[0].name,
                "time2si": constants[0][1] if constants[0] else None}

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
                    label = "{}: {}".format("AB"[i], source.get("path", "inline lists"))
                    if "path" in source and not source["path"].is_file():
                        label += " (missing)"
                    labels.append(label)
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
        if side not in {"a", "b"}:
            raise ValueError("Input side must be 'a' or 'b'")
        index = 0 if side == "a" else 1
        with self.data_lock:
            if index >= len(self.parameters):
                raise ValueError("Input side is unavailable")
            item = next(
                (
                    item for item in self.parameters[index]
                    if str(item["name"]).casefold() == key
                ),
                None,
            )
            if not item:
                raise ValueError("Parameter is not present in that input")
            update_parameter(
                self.paths[index], int(item["line"]),
                str(item["name"]), value.strip(),
            )
            self.reload()

    def operation_state(self):
        with self.operation_lock:
            return dict(self.operation_job)

    def autocomplete(self, scope, field_name, value):
        definitions = (
            operation_definitions() if scope == "operation"
            else plot_definitions() if scope == "plot" else []
        )
        field = next(
            (
                field for definition in definitions
                for field in definition["fields"]
                if field["name"] == field_name and field.get("path_kind")
            ),
            None,
        )
        if field is None:
            raise ValueError("Unknown path field")
        return path_completions(
            self.paths[0].parent, value, field["path_kind"],
            bool(field.get("multi")),
        )

    def start_operation(self, operation, values):
        command = jorek_operation_command(operation, values)
        environment = operation_environment(operation, values)
        preview = format_operation_command(operation, values)
        with self.operation_lock:
            if self.shutting_down:
                raise ValueError("The control panel is shutting down")
            if self.operation_process is not None:
                raise ValueError("Another operation is already running")
            try:
                process = subprocess.Popen(
                    command, cwd=str(self.paths[0].parent), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1,
                    start_new_session=True, env=environment,
                )
            except OSError as exc:
                raise ValueError("Cannot start operation: {}".format(exc))
            self.operation_process = process
            self.operation_job = {
                "status": "running", "operation": operation, "exit_code": None,
                "log": "$ cd {}\n$ {}\n".format(self.paths[0].parent, preview),
            }
            self.operation_thread = threading.Thread(
                target=self._capture_operation, args=(process,), daemon=True,
            )
            self.operation_thread.start()

    def _capture_operation(self, process):
        if process.stdout is not None:
            for line in iter(process.stdout.readline, ""):
                with self.operation_lock:
                    self.operation_job["log"] = (
                        self.operation_job["log"] + line
                    )[-200000:]
            process.stdout.close()
        return_code = process.wait()
        with self.operation_lock:
            self.operation_job["exit_code"] = return_code
            self.operation_job["status"] = "completed" if return_code == 0 else "failed"
            self.operation_job["log"] += "\n[process exited with status {}]\n".format(
                return_code
            )
            self.operation_process = None

    def stop_operation(self):
        with self.operation_lock:
            process = self.operation_process
            if process is None or process.poll() is not None:
                raise ValueError("No operation is running")
            self.operation_job["status"] = "stopping"
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()

    def clear_operation_log(self):
        with self.operation_lock:
            self.operation_job["log"] = ""

    def visualization_state(self):
        with self.visualization_lock:
            return dict(self.visualization_job)

    def start_visualization(self, plot_name, values):
        with self.visualization_lock:
            if self.shutting_down:
                raise ValueError("The control panel is shutting down")
            if self.visualization_process is not None:
                raise ValueError("Another visualization is already running")
            output_directory = Path(tempfile.mkdtemp(prefix="jorek-web-plots-"))
            try:
                with self.data_lock:
                    parameter_values = dict(self.values[0])
                command = jorek_plot_command(
                    plot_name, values, output_directory, parameter_values,
                    working_directory=self.paths[0].parent,
                )
                preview = format_plot_command(
                    plot_name, values, parameter_values,
                )
                process = subprocess.Popen(
                    command, cwd=str(self.paths[0].parent), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                shutil.rmtree(str(output_directory), ignore_errors=True)
                raise ValueError("Cannot start visualization: {}".format(exc))
            self.visualization_process = process
            self.visualization_previous_directory = self.visualization_directory
            self.visualization_directory = output_directory
            self.visualization_images = []
            self.visualization_figures = []
            self.visualization_job = {
                "status": "running", "plot": plot_name, "exit_code": None,
                "log": "$ cd {}\n$ {}\n".format(self.paths[0].parent, preview),
                "image_count": 0, "figure_count": 0, "figure_available": [],
            }
            self.visualization_thread = threading.Thread(
                target=self._capture_visualization, args=(process, output_directory),
                daemon=True,
            )
            self.visualization_thread.start()

    def _capture_visualization(self, process, output_directory):
        if process.stdout is not None:
            for line in iter(process.stdout.readline, ""):
                with self.visualization_lock:
                    self.visualization_job["log"] = (
                        self.visualization_job["log"] + line
                    )[-200000:]
            process.stdout.close()
        return_code = process.wait()
        images = sorted(output_directory.glob("*.png"))
        figures = {
            path.stem: path for path in sorted(output_directory.glob("*.mplfig"))
        }
        aligned_figures = [figures.get(path.stem) for path in images]
        with self.visualization_lock:
            self.visualization_images = images
            self.visualization_figures = aligned_figures
            self.visualization_job["image_count"] = len(images)
            self.visualization_job["figure_count"] = sum(
                path is not None for path in aligned_figures
            )
            self.visualization_job["figure_available"] = [
                path is not None for path in aligned_figures
            ]
            self.visualization_job["exit_code"] = return_code
            self.visualization_job["status"] = (
                "completed" if return_code == 0 and images else "failed"
            )
            self.visualization_job["log"] += (
                "\n[plot exited with status {}; {} figure(s) captured]\n"
                .format(return_code, len(images))
            )
            self.visualization_process = None
            previous_directory = self.visualization_previous_directory
            self.visualization_previous_directory = None
            if previous_directory is not None:
                shutil.rmtree(str(previous_directory), ignore_errors=True)

    def stop_visualization(self):
        with self.visualization_lock:
            process = self.visualization_process
            if process is None or process.poll() is not None:
                raise ValueError("No visualization is running")
            self.visualization_job["status"] = "stopping"
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            process.terminate()

    def visualization_image(self, index):
        with self.visualization_lock:
            if not 0 <= index < len(self.visualization_images):
                raise ValueError("Unknown visualization image")
            path = self.visualization_images[index]
            return path.read_bytes()

    def visualization_figure(self, index):
        with self.visualization_lock:
            if not 0 <= index < len(self.visualization_figures):
                raise ValueError("Interactive figure is unavailable")
            path = self.visualization_figures[index]
            if path is None:
                raise ValueError("Interactive figure is unavailable")
            return path.name, path.read_bytes()

    @staticmethod
    def _terminate_process(process):
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass

    def shutdown(self):
        """Terminate server-owned jobs and remove visualization artifacts."""
        with self.operation_lock:
            self.shutting_down = True
            operation_process = self.operation_process
        with self.visualization_lock:
            visualization_process = self.visualization_process
        self._terminate_process(operation_process)
        self._terminate_process(visualization_process)
        for thread in (self.operation_thread, self.visualization_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
        for process in (operation_process, visualization_process):
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    try:
                        process.kill()
                    except OSError:
                        pass
        for thread in (self.operation_thread, self.visualization_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2)
        with self.visualization_lock:
            for directory in (
                self.visualization_previous_directory, self.visualization_directory,
            ):
                if directory is not None:
                    shutil.rmtree(str(directory), ignore_errors=True)
            self.visualization_previous_directory = None
            self.visualization_directory = None

    def converted(self, key, rows, side):
        valid = [row for row in rows if len(row) >= 2]
        coordinate = [row[0] for row in valid]
        raw = [row[1] for row in valid]
        values, densities = self.values[side], density_constants(self.values[side])
        if key == "jsource_file": return raw, PLOT_CURRENT_DENSITY, None, None
        if key == "rho_file" and densities:
            return ([v * densities[0] for v in raw], PLOT_NUMBER_DENSITY,
                    [v * densities[1] for v in raw], PLOT_MASS_DENSITY)
        if key in {"ti_file", "te_file", "t_file"} and densities:
            return ([v / (elementary_charge * mu_0 * densities[0]) for v in raw], PLOT_T_EV,
                    [v / (Boltzmann * mu_0 * densities[0]) for v in raw], PLOT_T_K)
        if key in HEAT_SOURCE_FILE_PARAMETERS and densities:
            try:
                multiplier = parse_float(values[HEAT_SOURCE_FILE_PARAMETERS[key]])
            except (KeyError, ValueError):
                multiplier = math.nan
            factor = multiplier / ((GAMMA - 1) * mu_0 * math.sqrt(mu_0 * densities[1]))
            return [v * factor for v in raw], PLOT_HEAT_SOURCE, None, None
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
            return kappa, PLOT_KAPPA, chi, PLOT_CHI if chi is not None else None
        return None, "No SI conversion configured", None, None

    def plot(self, key, xmin=None, xmax=None):
        with self.data_lock:
            return self._plot(key, xmin, xmax)

    def _plot(self, key, xmin=None, xmax=None):
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
                converted.plot(
                    range(1, len(valid) + 1), [r[2] for r in valid],
                    style, color=colors[side], label=label + ": " + PLOT_PSI,
                )
                original.set(xlabel=PLOT_R, ylabel=PLOT_Z, title="Boundary shape"); original.set_aspect("equal", adjustable="datalim")
                converted.set(xlabel="Row ID", ylabel=PLOT_PSI, title="Psi by boundary row")
            elif columns >= 2:
                psi = [r[0] for r in valid]
                x = [math.sqrt(v) if v >= 0 else math.nan for v in psi]
                original.plot(x, [r[1] for r in valid], style, color=colors[side], label=label)
                y, ylabel, secondary_y, secondary_ylabel = self.converted(key, valid, side)
                if y is not None:
                    primary_label = label + ": " + ylabel
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
                        label=label + ": " + secondary_ylabel,
                    )
                    converted_secondary.set_ylabel(secondary_ylabel)
                original.set(xlabel=PLOT_X_PSI, ylabel="JOREK value", title="Original profile")
                converted.set(xlabel=PLOT_X_PSI, title="SI profile")
        for axis in (original, converted):
            axis.grid(True, alpha=.25)
            if axis.lines:
                # Keep the SI legend at upper left so it cannot overlap the
                # secondary-axis legend pinned at upper right.
                axis.legend(loc="upper left" if axis is converted else "best")
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
        def send(self, status, content, content_type, extra_headers=None):
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(content))); self.send_header("Cache-Control", "no-store")
            for name, value in (extra_headers or {}).items(): self.send_header(name, value)
            self.end_headers(); self.wfile.write(content)
        def do_GET(self):
            parsed = urlparse(self.path); query = parse_qs(parsed.query)
            try:
                if parsed.path == "/": return self.send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                if parsed.path == "/api/state": return self.send(200, json.dumps(app.state(), ensure_ascii=False).encode("utf-8"), "application/json")
                if parsed.path == "/api/operation":
                    return self.send(
                        200, json.dumps(app.operation_state()).encode("utf-8"),
                        "application/json",
                    )
                if parsed.path == "/api/visualization":
                    return self.send(
                        200, json.dumps(app.visualization_state()).encode("utf-8"),
                        "application/json",
                    )
                if parsed.path == "/api/visualization/image":
                    try:
                        index = int(query.get("index", ["0"])[0])
                    except ValueError:
                        return self.send(400, b"Invalid image index", "text/plain")
                    return self.send(200, app.visualization_image(index), "image/png")
                if parsed.path == "/api/visualization/figure":
                    try:
                        index = int(query.get("index", ["0"])[0])
                    except ValueError:
                        return self.send(400, b"Invalid figure index", "text/plain")
                    name, data = app.visualization_figure(index)
                    return self.send(
                        200, data, "application/octet-stream",
                        {"Content-Disposition": 'attachment; filename="{}"'.format(name)},
                    )
                if parsed.path == "/api/autocomplete":
                    matches = app.autocomplete(
                        query.get("scope", [""])[0],
                        query.get("field", [""])[0],
                        query.get("value", [""])[0],
                    )
                    return self.send(
                        200, json.dumps(matches).encode("utf-8"),
                        "application/json",
                    )
                if parsed.path == "/api/preview":
                    key = query.get("profile", [None])[0]
                    if key not in app.profiles:
                        return self.send(404, b"Unknown profile", "text/plain")
                    entry=app.profiles[key]; chunks=[]
                    for i,source in enumerate(entry["sources"]):
                        if source: chunks.append("===== INPUT {} =====\n{}".format("AB"[i],"\n".join(app.source_rows(source)[1])))
                    return self.send(200,json.dumps({"text":"\n\n".join(chunks)},ensure_ascii=False).encode("utf-8"),"application/json")
                if parsed.path == "/api/plot":
                    key = query.get("profile", [None])[0]
                    if key not in app.profiles:
                        return self.send(404, b"Unknown profile", "text/plain")
                    try:
                        lo, hi = float(query.get("xmin", [""])[0]), float(query.get("xmax", [""])[0])
                        if not (math.isfinite(lo) and math.isfinite(hi)) or lo >= hi:
                            lo = hi = None
                    except ValueError:
                        lo = hi = None
                    data=app.plot(key,lo,hi)
                    return self.send(200,data,"image/png")
                self.send(404,b"Not found","text/plain")
            except Exception as exc:
                self.send(500,str(exc).encode("utf-8"),"text/plain")
        def do_POST(self):
            try:
                length=int(self.headers.get("Content-Length","0")); data=json.loads(self.rfile.read(length).decode("utf-8"))
                path = urlparse(self.path).path
                if path == "/api/operation/run":
                    app.start_operation(data["operation"], data.get("values", {}))
                    return self.send(202, b'{"ok":true}', "application/json")
                if path == "/api/operation/stop":
                    app.stop_operation()
                    return self.send(200, b'{"ok":true}', "application/json")
                if path == "/api/operation/clear":
                    app.clear_operation_log()
                    return self.send(200, b'{"ok":true}', "application/json")
                if path == "/api/visualization/run":
                    app.start_visualization(data["plot"], data.get("values", {}))
                    return self.send(202, b'{"ok":true}', "application/json")
                if path == "/api/visualization/stop":
                    app.stop_visualization()
                    return self.send(200, b'{"ok":true}', "application/json")
                if path != "/api/edit": return self.send(404,b"{}","application/json")
                app.edit(data["key"],data["side"],data["value"])
                self.send(200,b'{"ok":true}',"application/json")
            except Exception as exc:
                self.send(400,json.dumps({"error":str(exc)}).encode("utf-8"),"application/json")
        def log_message(self, fmt, *args):
            print("[mhd-panel] " + fmt % args)
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
    print("MHD Control Panel: {}".format(url))
    if args.host in {"127.0.0.1","localhost"}: print("Remote access: ssh -L {0}:127.0.0.1:{0} user@server".format(args.port))
    if args.open_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        app.shutdown()
        server.server_close()


if __name__ == "__main__": main()
