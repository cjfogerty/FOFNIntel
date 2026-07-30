#!/usr/bin/env python3
"""
Build a Goldfish-style Schedule & Level Analysis dashboard for Barron Swim School
(O'Fallon, MO) from a manually-exported CSV (Barron's site has no public API, unlike
the iClass Pro competitors — see build_iclass_dashboard.py).

Source CSV columns: Class Name, Status, Spots Left, Age Range (Enrollment),
Session Type, Time, Days, Location, Tuition, Instructor:Student Ratio,
Program Age Range, Frequency/Duration

No instructor names or total capacity are published — capacity is DERIVED from the
Instructor:Student Ratio (e.g. "4:1" -> capacity 4 students per class). Day of week
is parsed from the Class Name (the CSV's own Days column reuses "T" for both
Tuesday and Thursday, so it can't be trusted).

Usage: python3 build_barron_dashboard.py barron_swim_classes_with_ratios.csv --date 2026-07-30
"""
import csv, re, json, argparse, os

LEVEL_ORDER = [
    "Parent & Tot I", "Parent & Tot II", "Little Junior", "Kinder Junior",
    "Beginner", "Advanced Junior", "Intermediate", "Intermediate II",
    "Advanced", "Elite Junior", "Elite Junior II", "Swim Abilities Waitlist",
]
DAY_RE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b")
DAY_FULL = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
            "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}


def parse_ratio(ratio):
    """returns (capacity, type)"""
    m = re.match(r"^\s*(\d+)\s*:\s*1", ratio or "")
    if not m:
        return 0, "Other"
    n = int(m.group(1))
    if n == 1:
        return 1, "Private"
    if n == 2:
        return 2, "Semi-Private"
    return n, "Group"


def level_of(name):
    if "Fall Swim Meet" in name:
        return "Special Event"
    lvl = DAY_RE.split(name, maxsplit=1)[0]
    lvl = re.sub(r"\s*-\s*$", "", lvl).strip()
    return lvl or "Other"


def to12h(t):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*", str(t or ""))
    return (m.group(1) + ":" + m.group(2) + " " + m.group(3).upper()) if m else ""


def build_records(rows):
    recs = []
    for r in rows:
        name = r["Class Name"].strip()
        cap, ctype = parse_ratio(r.get("Instructor:Student Ratio", ""))
        try:
            spots_left = int(r.get("Spots Left") or 0)
        except ValueError:
            spots_left = 0
        enrolled = max(cap - spots_left, 0) if cap else 0
        dmatch = DAY_RE.search(name)
        day = DAY_FULL.get(dmatch.group(1)) if dmatch else ""
        start = to12h((r.get("Time") or "").split("-")[0])
        recs.append({
            "name": name,
            "level": level_of(name),
            "type": ctype,
            "capacity": cap,
            "openings": spots_left,
            "awls": enrolled,
            "status": r.get("Status", ""),
            "day": day,
            "time": start,
            "sessionType": r.get("Session Type", ""),
            "tuition": r.get("Tuition", ""),
            "ageRange": r.get("Program Age Range", ""),
        })
    return recs


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ — Schedule & Level Analysis</title>
<style>
  :root{
    --bg:#121212; --panel:#1e1e1e; --panel2:#2a2a2a; --border:#333;
    --text:#e0e0e0; --muted:#9e9e9e;
    --red:#d32f2f; --amber:#ff9800; --green:#2e7d32;
    --h-none:#424242; --h1:#2e7d32; --h2:#f9a825; --h3:#ef6c00; --h4:#c62828;
    --accent:#42a5f5;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:Roboto,Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px}
  h1{font-size:26px;margin:0 0 4px}
  a.back{color:var(--accent);text-decoration:none;font-size:13px}
  .sub{color:var(--muted);text-align:center;margin-bottom:8px}
  .center{text-align:center}
  .note{max-width:920px;margin:6px auto 0;font-size:12px;color:var(--muted);text-align:center;line-height:1.5}
  .topbar{display:flex;gap:16px;justify-content:center;align-items:center;flex-wrap:wrap;margin:14px 0}
  .tabs{display:flex;background:var(--panel);border-radius:8px;overflow:hidden;margin:16px 0}
  .tab{flex:1;padding:16px;text-align:center;cursor:pointer;color:var(--muted);border:none;background:none;font-size:15px}
  .tab.active{background:#1565c0;color:#fff}
  .view{display:none}.view.active{display:block}
  .controls{display:flex;gap:16px;align-items:center;margin:16px 0;flex-wrap:wrap}
  select{background:var(--panel);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:10px}
  button.exp-btn{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:9px 14px;cursor:pointer}
  button.exp-btn:hover{border-color:var(--accent)}
  .summary{border-radius:8px;padding:16px 20px;margin:16px 0;display:flex;gap:48px;flex-wrap:wrap}
  .summary.blue{background:#5c85f0}.summary.teal{background:#4db6ac;color:#fff}
  .summary .lbl{font-size:13px;opacity:.85}.summary .val{font-size:22px;font-weight:700}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{padding:10px 12px;border-bottom:1px solid var(--border)}
  th{background:var(--panel2);text-align:left}
  td.c,th.c{text-align:center}
  td.r,th.r{text-align:right}
  .legend{display:flex;gap:20px;align-items:center;background:var(--panel);padding:12px 16px;border-radius:8px;margin:12px 0;flex-wrap:wrap}
  .swatch{display:inline-block;width:22px;height:22px;border-radius:4px;vertical-align:middle;margin-right:6px}
  .pill{display:inline-block;border-radius:12px;padding:2px 9px;font-size:12px;font-weight:600}
  .pill.open{background:#1b3a24;color:#66bb6a}
  .pill.wait{background:#3a1e1e;color:#ef9a9a}
</style>
</head>
<body>
<div class="center"><a class="back" href="index.html">&larr; FOFNIntel hub</a></div>
<h1 class="center">📅 __TITLE__ — Schedule &amp; Level Analysis</h1>
<div class="sub" id="locLine">Location: __LOCATION__ · 0 total classes · __DATE__</div>

<div class="topbar">
  <button class="exp-btn" id="csvBtn">⬇ Export CSV</button>
</div>

<div class="tabs">
  <button class="tab active" data-v="grid">📅 Schedule Grid</button>
  <button class="tab" data-v="level">📊 Summary by Level</button>
  <button class="tab" data-v="roster">📋 Full Class List</button>
</div>

<section class="view active" id="grid">
  <div class="controls">
    <label>Day of Week <select id="fDay"><option value="">All Days</option></select></label>
    <label>Level <select id="fLevel"><option value="">All Levels</option></select></label>
  </div>
  <div class="summary blue">
    <div><div class="lbl">Total Classes</div><div class="val" id="gTotal">0</div></div>
    <div><div class="lbl">Peak Time</div><div class="val" id="gPeak">—</div></div>
    <div><div class="lbl">Most Offered Level</div><div class="val" id="gMost">—</div></div>
  </div>
  <div class="legend">
    <b>Heat Map Legend</b>
    <span><span class="swatch" style="background:var(--h-none)"></span>None</span>
    <span><span class="swatch" style="background:var(--h1)"></span>1-2</span>
    <span><span class="swatch" style="background:var(--h2)"></span>3-4</span>
    <span><span class="swatch" style="background:var(--h3)"></span>5-6</span>
    <span><span class="swatch" style="background:var(--h4)"></span>7+</span>
  </div>
  <div style="overflow:auto"><table id="gridTable"></table></div>
</section>

<section class="view" id="level">
  <div class="summary blue">
    <div><div class="lbl">Total Capacity*</div><div class="val" id="lCap">0</div></div>
    <div><div class="lbl">Enrolled (est.)*</div><div class="val" id="lAwl">0</div></div>
    <div><div class="lbl">Open Spots</div><div class="val" id="lOpen">0</div></div>
    <div><div class="lbl">Utilization*</div><div class="val" id="lUtil">0%</div></div>
    <div><div class="lbl">Wait-Listed Classes</div><div class="val" id="lWait">0</div></div>
  </div>
  <table id="levelTable"></table>
</section>

<section class="view" id="roster">
  <div class="controls">
    <label>Status <select id="rStatus"><option value="">All</option><option value="Open">Open</option><option value="Wait List">Wait List</option></select></label>
  </div>
  <table id="rosterTable"></table>
</section>

<div class="note">
  Source: Barron Swim School public class catalog (manually exported) · pulled __DATE__.
  <b>*Capacity, Enrolled and Utilization are derived</b> from the published
  Instructor:Student ratio (e.g. 4:1 &rarr; capacity 4) since Barron does not publish
  capacity/enrolled directly. Enrolled = Capacity − Spots Left. Utilization colors:
  green ≥75%, amber 50–74%, red &lt;50%. Day of week is parsed from the class name
  (the source Days column reuses the same letter for Tuesday/Thursday).
</div>

<script>
const META = __META__;
const DATA = __DATA__;
const LEVEL_ORDER = __LEVELS__;
const DAY_ORDER = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];

const sum = (arr,f)=>arr.reduce((a,c)=>a+f(c),0);
function timeToMinutes(t){const m=t.match(/(\d+):(\d+)\s*(AM|PM)/i);if(!m)return 0;let h=+m[1]%12;if(/PM/i.test(m[3]))h+=12;return h*60+ +m[2];}
function heatColor(n){if(n===0)return"var(--h-none)";if(n<=2)return"var(--h1)";if(n<=4)return"var(--h2)";if(n<=6)return"var(--h3)";return"var(--h4)";}
function utilColor(u){return u>=75?"var(--green)":u>=50?"var(--amber)":"var(--red)";}

function renderLevel(){
  const byLevel={};
  DATA.forEach(c=>{const b=byLevel[c.level]||(byLevel[c.level]={cap:0,awls:0,open:0,wait:0});
    b.cap+=c.capacity;b.awls+=c.awls;b.open+=c.openings;if(c.status==="Wait List")b.wait++;});
  let g={cap:0,awls:0,open:0,wait:0},rows="";
  const ordered=LEVEL_ORDER.filter(l=>byLevel[l]).concat(Object.keys(byLevel).filter(l=>!LEVEL_ORDER.includes(l)));
  ordered.forEach(l=>{const b=byLevel[l];g.cap+=b.cap;g.awls+=b.awls;g.open+=b.open;g.wait+=b.wait;
    const util=b.cap?(b.awls/b.cap*100):0;
    rows+=`<tr><td>${l}</td><td class="r">${b.cap}</td>
      <td class="r" style="color:var(--red)">${b.awls}</td>
      <td class="r">${b.open}</td>
      <td class="r" style="color:${utilColor(util)};font-weight:600">${b.cap?util.toFixed(1)+"%":"—"}</td>
      <td class="r">${b.wait}</td></tr>`;});
  const gUtil=g.cap?(g.awls/g.cap*100):0;
  rows+=`<tr style="font-weight:700;background:var(--panel2)"><td>Grand Total</td>
    <td class="r">${g.cap}</td><td class="r">${g.awls}</td>
    <td class="r">${g.open}</td><td class="r">${gUtil.toFixed(1)}%</td><td class="r">${g.wait}</td></tr>`;
  document.getElementById("levelTable").innerHTML=
    `<tr><th>Level</th><th class="r">Total Capacity*</th><th class="r">Enrolled (est.)*</th>
     <th class="r">Open Spots</th><th class="r">Utilization*</th><th class="r">Wait-Listed</th></tr>`+rows;
  document.getElementById("lCap").textContent=g.cap;
  document.getElementById("lAwl").textContent=g.awls;
  document.getElementById("lOpen").textContent=g.open;
  document.getElementById("lUtil").textContent=gUtil.toFixed(1)+"%";
  document.getElementById("lWait").textContent=g.wait;
}

function renderGrid(){
  const day=document.getElementById("fDay").value, lvl=document.getElementById("fLevel").value;
  const data=DATA.filter(c=>c.day&&c.time&&(!day||c.day===day)&&(!lvl||c.level===lvl));
  const times=[...new Set(data.map(c=>c.time))].filter(Boolean).sort((a,b)=>timeToMinutes(a)-timeToMinutes(b));
  const cols=lvl?[lvl]:LEVEL_ORDER.filter(l=>DATA.some(c=>c.level===l&&c.day&&c.time));
  const counts={};
  data.forEach(c=>{(counts[c.time]||(counts[c.time]={}))[c.level]=((counts[c.time]||{})[c.level]||0)+1;});
  document.getElementById("gTotal").textContent=data.length;
  let peak="—",peakN=-1;
  times.forEach(t=>{const n=cols.reduce((a,l)=>a+((counts[t]||{})[l]||0),0);if(n>peakN){peakN=n;peak=`${t} (${n})`;}});
  document.getElementById("gPeak").textContent=peak;
  const lt={};data.forEach(c=>lt[c.level]=(lt[c.level]||0)+1);
  let most="—",mostN=-1;Object.entries(lt).forEach(([l,n])=>{if(n>mostN){mostN=n;most=`${l} (${n})`;}});
  document.getElementById("gMost").textContent=most;
  let head=`<tr><th>Time</th>${cols.map(l=>`<th class="c">${l}</th>`).join("")}<th class="c">Total</th></tr>`,body="";
  times.forEach(t=>{let rowTot=0;
    const cells=cols.map(l=>{const n=(counts[t]||{})[l]||0;rowTot+=n;
      return `<td class="c" style="background:${heatColor(n)}">${n||"-"}</td>`;}).join("");
    body+=`<tr><td>${t}</td>${cells}<td class="c">${rowTot}</td></tr>`;});
  document.getElementById("gridTable").innerHTML=head+body;
}

function renderRoster(){
  const st=document.getElementById("rStatus").value;
  const rows=DATA.filter(c=>!st||c.status===st).map(c=>
    `<tr><td>${c.name}</td><td>${c.level}</td><td>${c.day||"—"}</td><td>${c.time||"—"}</td>
     <td class="c"><span class="pill ${c.status==="Open"?"open":"wait"}">${c.status}</span></td>
     <td class="r">${c.openings}</td><td class="r">${c.capacity||"—"}</td>
     <td class="r">${c.tuition}</td></tr>`).join("");
  document.getElementById("rosterTable").innerHTML=
    `<tr><th>Class</th><th>Level</th><th>Day</th><th>Time</th><th class="c">Status</th>
     <th class="r">Spots Left</th><th class="r">Capacity*</th><th class="r">Tuition</th></tr>`+rows;
}

function initFilters(){
  const fd=document.getElementById("fDay");fd.innerHTML='<option value="">All Days</option>';
  DAY_ORDER.filter(d=>DATA.some(c=>c.day===d)).forEach(d=>fd.add(new Option(d,d)));
  const fl=document.getElementById("fLevel");fl.innerHTML='<option value="">All Levels</option>';
  LEVEL_ORDER.filter(l=>DATA.some(c=>c.level===l)).forEach(l=>fl.add(new Option(l,l)));
  fd.onchange=fl.onchange=renderGrid;
  document.getElementById("rStatus").onchange=renderRoster;
}
function exportCSV(){
  const hdr=["Class","Level","Day","Time","Status","Spots Left","Capacity*","Tuition"];
  const rows=DATA.map(c=>[c.name,c.level,c.day,c.time,c.status,c.openings,c.capacity,c.tuition]);
  const esc=v=>{v=String(v==null?"":v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
  const csv=[hdr.join(",")].concat(rows.map(r=>r.map(esc).join(","))).join("\n");
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));
  a.download=("barron_"+META.date+".csv").replace(/[^a-z0-9_.-]+/gi,"_");
  a.click();
}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));
  t.classList.add("active");document.getElementById(t.dataset.v).classList.add("active");});
document.getElementById("csvBtn").onclick=exportCSV;
document.getElementById("locLine").textContent=`Location: __LOCATION__ · ${DATA.length} total classes · __DATE__`;
initFilters();renderGrid();renderLevel();renderRoster();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--title", default="Barron Swim School")
    ap.add_argument("--location", default="O'Fallon, MO")
    ap.add_argument("--date", default="")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()

    with open(a.csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    data = build_records(rows)
    levels = LEVEL_ORDER + sorted({r["level"] for r in data if r["level"] not in LEVEL_ORDER})
    meta = {"title": a.title, "date": a.date or "(undated)"}
    html = (TEMPLATE
            .replace("__TITLE__", a.title)
            .replace("__LOCATION__", a.location)
            .replace("__DATE__", a.date or "(undated)")
            .replace("__META__", json.dumps(meta))
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__LEVELS__", json.dumps(levels)))
    outfn = os.path.join(a.out, "barron.html")
    with open(outfn, "w") as f:
        f.write(html)
    print("wrote", outfn, "(%d KB, %d classes)" % (len(html) // 1024, len(data)))


if __name__ == "__main__":
    main()
