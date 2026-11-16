#!/usr/bin/env python3
"""NXT Blocks v2: Scratch-style block programming for a LEGO NXT brick, built on NXT-Python.

Run:  source ~/nxt-env/bin/activate && python3 nxt_blocks_v2.py
Fixed wiring: touch sensors on ports 1 and 2, color sensor on port 3, ultrasonic on port 4,
motors on ports A, B and C. The page is served only on this Mac (127.0.0.1).
"""
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_lib = getattr(sys, "_MEIPASS", None)  # set when running as the packaged app
os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (_lib + ":" if _lib else "") + "/opt/homebrew/lib"
if sys.stdout is None:
    sys.stdout = sys.stderr = open(os.devnull, "w")

import nxt.locator
import nxt.motor
import nxt.sensor
import nxt.sensor.generic as gen
import usb.core

# On macOS the NXT is already configured; ignore "No such device" from set_configuration().
_set_cfg = usb.core.Device.set_configuration


def _safe_set_cfg(self, *args, **kwargs):
    try:
        return _set_cfg(self, *args, **kwargs)
    except usb.core.USBError:
        pass


usb.core.Device.set_configuration = _safe_set_cfg

PORT = 8766
TOKEN = secrets.token_urlsafe(16)
STATE = {"running": False, "log": [], "stop": threading.Event(), "sensors": {}}
SENSORS = {"t1": (1, gen.Touch, "Touch 1"), "t2": (2, gen.Touch, "Touch 2"),
           "color": (3, gen.Color, "Color"), "dist": (4, gen.Ultrasonic, "Distance")}


class Stopped(Exception):
    pass


class Runner:
    """Interprets the block program. Only known block types are ever executed."""

    def __init__(self, brick, stop, log):
        self.b, self.stop, self.log, self.cache = brick, stop, log, {}

    def nap(self, sec):
        end = time.time() + sec
        while True:
            if self.stop.is_set():
                raise Stopped
            left = end - time.time()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def motors(self, name):
        out = []
        for c in str(name).split("+"):
            if c not in ("A", "B", "C"):
                raise ValueError("bad motor: " + c)
            if "m" + c not in self.cache:
                self.cache["m" + c] = self.b.get_motor(getattr(nxt.motor.Port, c))
            out.append(self.cache["m" + c])
        return out

    def sensor(self, k):
        if k not in self.cache:
            port, cls, _ = SENSORS[k]
            self.cache[k] = self.b.get_sensor(getattr(nxt.sensor.Port, "S%d" % port), cls)
        return self.cache[k]

    def test(self, c):
        k, op, v = c["k"], c["op"], c.get("v")
        val = self.sensor(k).get_sample()
        if k in ("t1", "t2"):
            return bool(val) == (op == "pressed")
        if k == "dist":
            return val < float(v) if op == "<" else val > float(v)
        return (val.name.lower() == str(v).lower()) == (op == "is")

    def run(self, blocks):
        for blk in blocks:
            if self.stop.is_set():
                raise Stopped
            t, a, c = blk.get("t"), blk.get("a", {}), blk.get("c", [])
            if t in ("motor_on", "motor_for"):
                power = max(-100, min(100, int(float(a["p"]))))
                ms = self.motors(a["m"])
                for m in ms:
                    m.run(power)
                if t == "motor_for":
                    self.nap(max(0.0, float(a["s"])))
                    for m in ms:
                        m.brake()
            elif t == "motor_off":
                for m in self.motors(a["m"]):
                    m.brake()
            elif t == "wait":
                self.nap(max(0.0, float(a["s"])))
            elif t == "wait_until":
                while not self.test(blk["cond"]):
                    self.nap(0.03)
            elif t == "show":
                v = self.sensor(a["k"]).get_sample()
                self.log.append("%s: %s" % (SENSORS[a["k"]][2], getattr(v, "name", v)))
            elif t == "beep":
                ms = max(10, int(float(a["ms"])))
                self.b.play_tone(int(float(a["hz"])), ms)
                self.nap(ms / 1000.0)
            elif t == "repeat":
                for _ in range(max(0, int(float(a["n"])))):
                    self.run(c)
                    self.nap(0.02)
            elif t == "forever":
                while True:
                    self.run(c)
                    self.nap(0.03)
            elif t == "if":
                if self.test(blk["cond"]):
                    self.run(c)
            elif t == "ifelse":
                self.run(c if self.test(blk["cond"]) else blk.get("c2", []))
            elif t == "monitor":
                while True:
                    vals = {}
                    for k in SENSORS:
                        try:
                            v = self.sensor(k).get_sample()
                            vals[k] = getattr(v, "name", v)
                        except Exception:
                            vals[k] = None
                    STATE["sensors"] = vals
                    self.nap(0.3)
            elif t == "until":
                while not self.test(blk["cond"]):
                    self.run(c)
                    self.nap(0.03)
            else:
                raise ValueError("unknown block: %r" % (t,))


def worker(prog, stop, log):
    try:
        log.append("Connecting to brick...")
        with nxt.locator.find() as brick:
            log.append("Connected: %s" % brick.get_device_info()[0])
            r = Runner(brick, stop, log)
            try:
                r.run(prog)
            finally:
                for key, m in list(r.cache.items()):
                    if key[0] == "m":
                        try:
                            m.idle()
                        except Exception:
                            pass
        log.append("Finished.")
    except Stopped:
        log.append("Stopped.")
    except Exception as e:
        log.append("Error: %s" % e)
    finally:
        STATE["running"] = False
        STATE["sensors"] = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        return self.headers.get("Host") in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT)

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, "{}")
        if self.path == "/":
            self.send(200, PAGE.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        elif self.path == "/status":
            self.send(200, json.dumps({"running": STATE["running"], "log": STATE["log"][-200:], "sensors": STATE["sensors"]}))
        else:
            self.send(404, "{}")

    def do_POST(self):
        if not self.host_ok() or self.headers.get("X-Token") != TOKEN:
            return self.send(403, "{}")
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/quit":
            self.send(200, "{}")
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return
        if self.path == "/stop":
            STATE["stop"].set()
            return self.send(200, "{}")
        if self.path == "/run":
            if STATE["running"]:
                return self.send(200, json.dumps({"error": "Already running. Press Stop first."}))
            try:
                prog = json.loads(raw)
                assert isinstance(prog, list)
            except Exception:
                return self.send(200, json.dumps({"error": "Bad program."}))
            STATE["stop"], STATE["log"], STATE["running"] = threading.Event(), [], True
            threading.Thread(target=worker, args=(prog, STATE["stop"], STATE["log"]), daemon=True).start()
            return self.send(200, "{}")
        self.send(404, "{}")


PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>NXT Blocks</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:14px system-ui,sans-serif;background:#f9f9f9;color:#222;height:100vh;display:flex;flex-direction:column}
header{background:#4c97ff;color:#fff;padding:8px 14px;display:flex;gap:8px;align-items:center}
header h1{font-size:17px;margin:0 auto 0 0}
button{font:inherit;border:0;border-radius:16px;padding:5px 14px;cursor:pointer}
#run{background:#4cbf56;color:#fff}#stop{background:#ec5959;color:#fff}
main{flex:1;display:flex;min-height:0}
#lt{display:flex;width:310px;background:#fff;border-right:1px solid #ddd}
#cats{width:96px;display:flex;flex-direction:column;gap:4px;padding:8px 0}
.cat{background:none;border-radius:0;text-align:left;padding:10px 6px}.cat.on{background:#e8f0ff;font-weight:600}
#blocks{flex:1;padding:10px 8px;overflow:auto}
#blocks .blk{margin-bottom:10px;cursor:grab}
#prog{flex:1;padding:16px;overflow:auto;background:#f0f2f5}
#rt{width:250px;padding:10px;background:#fff;border-left:1px solid #ddd;overflow:auto}
.blk{--c:#888;position:relative}
.top,.bot{background:var(--c);color:#fff;padding:7px 9px;display:flex;flex-wrap:wrap;align-items:center;gap:5px;font-weight:600}
.blk:not(.cb)>.top{border-radius:5px}
.cb>.top{border-radius:5px 5px 0 0}
.bot{height:16px;padding:0;border-radius:0 0 5px 5px}
.kids{border-left:14px solid var(--c);min-height:8px}
.top::before{content:"";position:absolute;left:14px;top:-4px;width:22px;height:5px;background:var(--c);border-radius:4px 4px 0 0}
.hat{background:#ffbf00;color:#fff;font-weight:600;padding:12px 14px 7px;border-radius:20px 20px 5px 5px;width:fit-content}
.slot{height:4px}.slot.on{height:26px;background:rgba(0,0,0,.12);border-radius:4px}
.kids>.slot:only-child{height:22px}
input,select{border:0;border-radius:11px;padding:2px 8px;background:#fff;color:#333;font:inherit;font-weight:400}
input{width:58px}
.pill{background:#fff;color:#333;border-radius:11px;padding:1px 8px;font-weight:400}
.cond{display:inline-flex;gap:3px;align-items:center;background:rgba(0,0,0,.22);padding:2px 14px;clip-path:polygon(10px 0,calc(100% - 10px) 0,100% 50%,calc(100% - 10px) 100%,10px 100%,0 50%)}
.del{margin-left:auto;background:rgba(0,0,0,.2);color:#fff;padding:0 7px;border-radius:9px}
pre{background:#222;color:#9f9;padding:8px;border-radius:6px;min-height:160px;white-space:pre-wrap}
.hint{color:#666;font-size:12px}
header{flex-wrap:wrap}.els{background:var(--c);color:#fff;padding:3px 9px;font-weight:600}#cm{position:fixed;inset:5%;background:#fff;border:1px solid #ccc;border-radius:10px;padding:12px;display:none;flex-direction:column;gap:8px;box-shadow:0 6px 30px #0006}#cm pre{flex:1;margin:0;overflow:auto;background:#222;color:#eee}
</style></head><body>
<header><h1>NXT Blocks</h1><span id="st">idle</span>
<button id="ex">Example</button><button id="clr">Clear</button><button id="save">Save</button><button id="load">Load</button><button id="sens">Sensors</button><button id="pybtn">Show code</button><input id="file" type="file" accept=".json" hidden>
<button id="run">&#9654; Run</button><button id="stop">&#9632; Stop</button><button id="quit">Quit</button></header>
<main><div id="lt"><div id="cats"></div><div id="blocks"></div></div>
<div id="prog"></div>
<div id="rt"><b>Sensors</b><pre id="sv" style="min-height:90px;color:#9cf">Press Sensors to start</pre><b>Log</b><pre id="log"></pre>
<p class="hint">Touch 1 and 2: ports 1, 2. Color: port 3. Distance (ultrasonic): port 4. Motors: A, B, C.<br>Drag blocks from the left into the script, drag them back to the left to delete, or click a block to add it at the end.</p></div></main>
<div id="cm"><div><b>Equivalent Python</b> <button id="cp1">Copy</button> <button id="cp2">Save .py</button> <button id="cp3">Close</button></div><pre id="cp"></pre></div>
<script>
const TOKEN="__TOKEN__",$=id=>document.getElementById(id);
const MT=['A','B','C','A+B','A+C','B+C','A+B+C'];
const SN=[['t1','Touch 1'],['t2','Touch 2'],['dist','Distance'],['color','Color']];
const COL=['black','blue','green','yellow','red','white'];
const OPS={t1:['pressed','not pressed'],t2:['pressed','not pressed'],dist:['<','>'],color:['is','is not']};
const M={k:'m',o:MT,d:'A'},CD={cond:1};
const D={
 motor_on:['Motor',M,'on at power',{k:'p',d:60}],
 motor_for:['Run motor',M,'at power',{k:'p',d:60},'for',{k:'s',d:1},'sec'],
 motor_off:['Stop motor',M],
 wait_until:['Wait until',CD],
 show:['Show',{k:'k',o:SN,d:'dist'},'in log'],
 wait:['Wait',{k:'s',d:1},'seconds'],
 repeat:['Repeat',{k:'n',d:10},'times'],
 forever:['Forever'],
 if:['If',CD,'then'],
 ifelse:['If',CD,'then ... else'],
 until:['Repeat until',CD],
 beep:['Beep',{k:'hz',d:440},'Hz for',{k:'ms',d:300},'ms']};
const BOX={repeat:1,forever:1,if:1,ifelse:1,until:1};
const CATS=[{n:'Motors',c:'#4c97ff',b:['motor_on','motor_for','motor_off']},
 {n:'Sensing',c:'#2fb4d8',b:['wait_until','show']},
 {n:'Control',c:'#ffab19',b:['wait','repeat','forever','if','ifelse','until']},
 {n:'Sound',c:'#cf63cf',b:['beep']}];
const CC={};CATS.forEach(c=>c.b.forEach(t=>CC[t]=c.c));
let prog=[],cur=0,drag=null;
try{prog=JSON.parse(localStorage.getItem('nxtprog2'))||[];if(!Array.isArray(prog))prog=[]}catch(e){prog=[]}
function save(){try{localStorage.setItem('nxtprog2',JSON.stringify(prog))}catch(e){}}
function mk(tag,txt,cls){const e=document.createElement(tag);if(txt)e.textContent=txt;if(cls)e.className=cls;return e}
function sel(o,v,fn){const s=mk('select');o.forEach(x=>{const p=Array.isArray(x)?x:[x,x],op=mk('option',p[1]);op.value=p[0];s.append(op)});s.value=v;s.onchange=()=>fn(s.value);return s}
function num(v,fn){const i=mk('input');i.type='number';i.step='any';i.value=v;i.onchange=()=>fn(parseFloat(i.value));return i}
function condw(c){
 const w=mk('span','','cond');
 w.append(sel(SN,c.k,v=>{c.k=v;c.op=OPS[v][0];c.v=v=='color'?'red':20;draw()}),sel(OPS[c.k],c.op,v=>{c.op=v;save()}));
 if(c.k=='dist')w.append(num(c.v,v=>{c.v=v;save()}));
 if(c.k=='color')w.append(sel(COL,c.v,v=>{c.v=v;save()}));
 return w}
function nb(t){const b={t,a:{}};D[t].forEach(x=>{if(x.k)b.a[x.k]=x.d;if(x.cond)b.cond={k:'t1',op:'pressed',v:20}});if(BOX[t])b.c=[];if(t=='ifelse')b.c2=[];return b}
function inside(b,L){const f=A=>!!A&&(A===L||A.some(x=>inside(x,L)));return f(b.c)||f(b.c2)}
function put(L,i){
 if(!drag)return;let b;
 if(drag.t)b=nb(drag.t);
 else{if(inside(drag.b,L))return;const j=drag.L.indexOf(drag.b);drag.L.splice(j,1);if(drag.L===L&&j<i)i--;b=drag.b}
 L.splice(i,0,b);drag=null;draw()}
function slot(L,i){
 const s=mk('div','','slot');
 s.ondragover=e=>{e.preventDefault();s.classList.add('on')};
 s.ondragleave=()=>s.classList.remove('on');
 s.ondrop=e=>{e.preventDefault();e.stopPropagation();put(L,i)};
 return s}
function list(L,cls){const d=mk('div','',cls);L.forEach((b,i)=>d.append(slot(L,i),block(b,L)));d.append(slot(L,L.length));return d}
function block(b,L){
 const e=mk('div','','blk'+(BOX[b.t]?' cb':'')),top=mk('div','','top');
 e.style.setProperty('--c',CC[b.t]);e.draggable=true;
 e.ondragstart=ev=>{ev.stopPropagation();drag={b,L};ev.dataTransfer.setData('text','b')};
 D[b.t].forEach(x=>{
  if(typeof x=='string')top.append(x);
  else if(x.cond)top.append(condw(b.cond));
  else top.append(x.o?sel(x.o,b.a[x.k],v=>{b.a[x.k]=v;save()}):num(b.a[x.k],v=>{b.a[x.k]=v;save()}))});
 const del=mk('button','\u2715','del');del.onclick=()=>{L.splice(L.indexOf(b),1);draw()};top.append(del);
 e.append(top);
 if(BOX[b.t]){e.append(list(b.c,'kids'));if(b.t=='ifelse')e.append(mk('div','else','els'),list(b.c2,'kids'));e.append(mk('div','','bot'))}
 return e}
function draw(){const r=$('prog');r.innerHTML='';r.append(mk('div','\u25B6 when Run clicked','hat'),list(prog));save()}
function pal(){
 $('cats').innerHTML='';
 CATS.forEach((c,i)=>{const b=mk('button',c.n,i==cur?'cat on':'cat');b.style.borderLeft='8px solid '+c.c;b.onclick=()=>{cur=i;pal()};$('cats').append(b)});
 const p=$('blocks');p.innerHTML='';
 CATS[cur].b.forEach(t=>{
  const e=mk('div','','blk'+(BOX[t]?' cb':'')),top=mk('div','','top');e.style.setProperty('--c',CC[t]);
  D[t].forEach(x=>top.append(typeof x=='string'?x:x.cond?mk('span','condition','cond'):mk('span',String(x.d),'pill')));
  e.append(top);if(BOX[t]){e.append(mk('div','','kids'));if(t=='ifelse')e.append(mk('div','else','els'),mk('div','','kids'));e.append(mk('div','','bot'))}
  e.draggable=true;e.ondragstart=ev=>{drag={t};ev.dataTransfer.setData('text','p')};
  e.onclick=()=>{prog.push(nb(t));draw()};p.append(e)})}
$('lt').ondragover=e=>e.preventDefault();
$('lt').ondrop=()=>{if(drag&&drag.b){drag.L.splice(drag.L.indexOf(drag.b),1);drag=null;draw()}};
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'X-Token':TOKEN,'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
$('run').onclick=()=>post('/run',prog).then(r=>{if(r.error)alert(r.error)});
$('stop').onclick=()=>post('/stop');
$('quit').onclick=()=>post('/quit').then(()=>{document.body.innerHTML='<p style="padding:24px">NXT Blocks has quit. You can close this tab.</p>'});
$('clr').onclick=()=>{prog=[];draw()};
$('ex').onclick=()=>{prog=[{t:'forever',a:{},c:[
 {t:'if',a:{},cond:{k:'t1',op:'pressed',v:20},c:[{t:'motor_on',a:{m:'A+C',p:60}}]},
 {t:'if',a:{},cond:{k:'t1',op:'not pressed',v:20},c:[{t:'motor_off',a:{m:'A+C'}}]}]}];draw()};
setInterval(async()=>{try{const s=await(await fetch('/status')).json();
 $('log').textContent=s.log.join('\n');
 const v=s.sensors||{},tf=x=>x==null?'n/a':x?'pressed':'not pressed';
 $('sv').textContent=Object.keys(v).length?['Touch 1: '+tf(v.t1),'Touch 2: '+tf(v.t2),'Color: '+(v.color||'n/a'),'Distance: '+(v.dist==null?'n/a':v.dist+' cm')].join('\n'):'Press Sensors to start';$('st').textContent=s.running?'\u25CF running':'idle'}
 catch(e){$('st').textContent='server not reachable'}},500);
const SV={t1:['touch1',1,'Touch'],t2:['touch2',2,'Touch'],color:['color',3,'Color'],dist:['dist',4,'Ultrasonic']};
let used;
function cx(c){used.add(c.k);const v=SV[c.k][0];
 if(c.k=='t1'||c.k=='t2')return (c.op=='pressed'?'':'not ')+v+'.get_sample()';
 if(c.k=='dist')return v+'.get_sample() '+c.op+' '+c.v;
 return 'color.get_sample().name.lower() '+(c.op=='is'?'==':'!=')+' "'+c.v+'"'}
function pl(L,d){const o=L.map(b=>ps(b,d)).join('');return o||d+'pass\n'}
function ps(b,d){const a=b.a,m=JSON.stringify(a.m),n=d+'    ';
 switch(b.t){
 case 'motor_on':return d+'for m in motors('+m+'): m.run('+a.p+')\n';
 case 'motor_for':return d+'for m in motors('+m+'): m.run('+a.p+')\n'+d+'time.sleep('+a.s+')\n'+d+'for m in motors('+m+'): m.brake()\n';
 case 'motor_off':return d+'for m in motors('+m+'): m.brake()\n';
 case 'wait':return d+'time.sleep('+a.s+')\n';
 case 'wait_until':return d+'while not ('+cx(b.cond)+'): time.sleep(0.03)\n';
 case 'show':used.add(a.k);return d+'print('+JSON.stringify(SN.find(x=>x[0]==a.k)[1])+', '+SV[a.k][0]+'.get_sample())\n';
 case 'beep':return d+'brick.play_tone('+a.hz+', '+a.ms+')\n'+d+'time.sleep('+a.ms/1000+')\n';
 case 'repeat':return d+'for _ in range('+a.n+'):\n'+pl(b.c,n)+n+'time.sleep(0.02)\n';
 case 'forever':return d+'while True:\n'+pl(b.c,n)+n+'time.sleep(0.03)\n';
 case 'if':return d+'if '+cx(b.cond)+':\n'+pl(b.c,n);
 case 'ifelse':return d+'if '+cx(b.cond)+':\n'+pl(b.c,n)+d+'else:\n'+pl(b.c2,n);
 case 'until':return d+'while not ('+cx(b.cond)+'):\n'+pl(b.c,n)+n+'time.sleep(0.03)\n'}
 return ''}
function code(){used=new Set();const body=pl(prog,'        ');
 let h='import time\n\nimport nxt.locator\nimport nxt.motor\nimport nxt.sensor\nimport nxt.sensor.generic as gen\n\n\nwith nxt.locator.find() as brick:\n    def motors(names):\n        return [brick.get_motor(getattr(nxt.motor.Port, c)) for c in names.split("+")]\n\n';
 used.forEach(k=>{h+='    '+SV[k][0]+' = brick.get_sensor(nxt.sensor.Port.S'+SV[k][1]+', gen.'+SV[k][2]+')\n'});
 return h+'    try:\n'+body+'    finally:\n        for m in motors("ABC"):\n            m.idle()\n'}
function dl(n,t,ty){const a=mk('a');a.href=URL.createObjectURL(new Blob([t],{type:ty}));a.download=n;a.click()}
const ok=L=>Array.isArray(L)&&L.every(b=>b&&D[b.t]&&(!b.c||ok(b.c))&&(!b.c2||ok(b.c2)));
$('save').onclick=()=>dl('nxt-program.json',JSON.stringify(prog,null,1),'application/json');
$('load').onclick=()=>$('file').click();
$('file').onchange=async()=>{try{const L=JSON.parse(await $('file').files[0].text());if(!ok(L))throw 0;prog=L;draw()}catch(e){alert('That file is not an NXT Blocks program.')}$('file').value=''};
$('sens').onclick=()=>post('/run',[{t:'monitor'}]).then(r=>{if(r.error)alert(r.error)});
$('pybtn').onclick=()=>{$('cp').textContent=code();$('cm').style.display='flex'};
$('cp1').onclick=()=>navigator.clipboard.writeText($('cp').textContent);
$('cp2').onclick=()=>dl('nxt_program.py',$('cp').textContent,'text/x-python');
$('cp3').onclick=()=>{$('cm').style.display='none'};
pal();draw();
</script></body></html>"""


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import usb.backend.libusb1 as lu
        ok = lu.get_backend() is not None
        print("libusb:", "ok" if ok else "NOT FOUND")
        sys.exit(0 if ok else 1)
    url = "http://127.0.0.1:%d/" % PORT
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print("NXT Blocks is already running; opening it in the browser.")
        webbrowser.open(url)
        raise SystemExit
    print("NXT Blocks v2 is running at", url, "- press Ctrl-C here to quit.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
