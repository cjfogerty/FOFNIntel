#!/usr/bin/env python3
"""
Build a Goldfish-style Schedule & Instructor Analysis dashboard for an iClass Pro
account, hosted on FOFNIntel next to CO Swim School & Little Kickers.

Data source: iClass Pro public open API (no auth). Fields available publicly are
`openings` + `futureOpenings` + instructors/schedule. Capacity/enrolled are NOT
published, so they're DERIVED (see derive_capacity):
   * Private lessons  -> capacity 1   (1:1, data-confirmed max openings == 1)
   * Semi-Private     -> capacity 2   (2:1, data-confirmed max openings == 2)
   * Group/other      -> capacity = max openings seen for that level (an empty
                         class exposes its full size), floor 4.
   enrolled (AWLs) = capacity - openings ;  utilization = enrolled / capacity.

Usage:  python3 build_iclass_dashboard.py tsswim --title "TS Swim" --date 2026-07-21
"""
import urllib.request, ssl, json, re, argparse, os
from collections import defaultdict

API = "https://app.iclasspro.com/api/open/v1"
UA  = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
DAYS = {"Sun":"Sunday","Mon":"Monday","Tue":"Tuesday","Wed":"Wednesday",
        "Thu":"Thursday","Fri":"Friday","Sat":"Saturday"}
LEVEL_ORDER = ["Parent & Baby","Parent & Baby/Toddler","Parent & Toddler",
    "Toddler Transition","4/5 Year","6/7 Year","8/9 Year","10 & Up",
    "Competitive Stroke","Technique & Conditioning 1","Technique & Conditioning 2",
    "Adult","Sibling-Friend","Special Needs","Private Any Age"]


def api(account, path):
    req = urllib.request.Request(API + "/" + account + path,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_all(account, location_id=None):
    loc = ("&locationId=%d" % location_id) if location_id else ""
    out = []
    for pg in range(1, 60):
        j = api(account, "/classes?limit=50&page=%d%s" % (pg, loc))
        rows = j.get("data", [])
        if not rows:
            break
        out += rows
        if pg * 50 >= j.get("totalRecords", 0):
            break
    return out


def to12h(t):
    if not t:
        return ""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*$", str(t))
    return (m.group(1) + ":" + m.group(2) + " " + m.group(3).upper()) if m else str(t)


def class_type(name):
    u = (name or "").upper()
    if "SEMI" in u:    return "Semi-Private"
    if "PRIVATE" in u: return "Private"
    return "Group"


def canonical_level(name):
    u = re.sub(r"\s+", " ", (name or "")).upper()
    rules = [
        (("PARENT & BABY", "TODDLER"), "Parent & Baby/Toddler"),
        (("6-36",),                    "Parent & Baby/Toddler"),
        (("PARENT & BABY",),           "Parent & Baby"),
        (("6-18",),                    "Parent & Baby"),
        (("PARENT & TODDLER",),        "Parent & Toddler"),
        (("19-36",),                   "Parent & Toddler"),
        (("TODDLER TRANSITION",),      "Toddler Transition"),
        (("4/5",),                     "4/5 Year"),
        (("6/7",),                     "6/7 Year"),
        (("8/9",),                     "8/9 Year"),
        (("10 & UP",),                 "10 & Up"),
        (("10 AND UP",),               "10 & Up"),
        (("COMPETITIVE",),             "Competitive Stroke"),
        (("TECHNIQUE", "2"),           "Technique & Conditioning 2"),
        (("TECHNIQUE",),               "Technique & Conditioning 1"),
        (("ADULT",),                   "Adult"),
        (("SIBLING",),                 "Sibling-Friend"),
        (("SPECIAL NEEDS",),           "Special Needs"),
        (("PRIVATE",),                 "Private Any Age"),
    ]
    for needles, label in rules:
        if all(n in u for n in needles):
            return label
    return re.split(r"\s+/\s+", (name or "").strip())[0].title() or "Other"


def derive_group_caps(classes):
    """capacity for Group classes = max openings seen per canonical level (empty class = full size)."""
    caps = defaultdict(int)
    for c in classes:
        if class_type(c["name"]) == "Group":
            caps[canonical_level(c["name"])] = max(caps[canonical_level(c["name"])], c.get("openings") or 0)
    return caps


def capacity_of(c, group_caps):
    t = class_type(c["name"])
    if t == "Private":      return 1
    if t == "Semi-Private": return 2
    return max(group_caps.get(canonical_level(c["name"]), 0), (c.get("openings") or 0), 4)


def build_records(classes):
    group_caps = derive_group_caps(classes)
    recs = []
    for c in classes:
        openings = c.get("openings") or 0
        fut = c.get("futureOpenings") or 0
        cap = capacity_of(c, group_caps)
        enrolled = max(cap - openings, 0)
        level = canonical_level(c["name"])
        ctype = class_type(c["name"])
        instr = ", ".join(c.get("instructors") or []) or ""
        display = re.sub(r"\s+", " ", c["name"]).strip().title()
        for sc in (c.get("schedule") or [{}]):
            start = to12h(sc.get("startTime"))
            recs.append({
                "name": display,
                "levelId": c.get("levelId"),
                "level": level,
                "type": ctype,
                "capacity": cap,
                "openings": openings,
                "futureOpenings": fut,
                "awls": enrolled,
                "day": DAYS.get(sc.get("dayName"), sc.get("dayName") or ""),
                "time": start,
                "instructor": instr,
            })
    return recs


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ — Schedule & Instructor Analysis</title>
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
  .chip{display:inline-block;border:1px solid var(--border);border-radius:14px;padding:2px 10px;margin:2px;font-size:12px;background:var(--panel2)}
  .exp{cursor:pointer;color:var(--accent);user-select:none}
  .detail{background:var(--panel);padding:14px 20px;font-size:13px;color:var(--muted)}
  .detail b{color:var(--text)}
</style>
</head>
<body>
<div class="center"><a class="back" href="index.html">&larr; FOFNIntel hub</a></div>
<h1 class="center">📅 __TITLE__ — Schedule &amp; Instructor Analysis</h1>
<div class="sub" id="locLine">Location: — · 0 total classes</div>

<div class="topbar">
  <label>Location
    <select id="locSel"></select>
  </label>
  <button class="exp-btn" id="csvBtn">⬇ Export CSV</button>
</div>

<div class="tabs">
  <button class="tab active" data-v="grid">📅 Schedule Grid</button>
  <button class="tab" data-v="inst">👥 Instructors</button>
  <button class="tab" data-v="level">📊 Summary by Level</button>
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

<section class="view" id="inst">
  <div class="controls">
    <label>Sort By <select id="iSort">
      <option value="classes">Total Classes</option>
      <option value="name">Name</option>
      <option value="spec">Specialization</option>
    </select></label>
  </div>
  <div class="summary teal">
    <div><div class="lbl">Total Instructors</div><div class="val" id="iCount">0</div></div>
    <div><div class="lbl">Total Classes</div><div class="val" id="iClasses">0</div></div>
    <div><div class="lbl">Avg Classes / Instructor</div><div class="val" id="iAvg">0</div></div>
  </div>
  <table id="instTable"></table>
  <p class="sub">Click any row to see detailed breakdown by day and level</p>
</section>

<section class="view" id="level">
  <div class="summary blue">
    <div><div class="lbl">Total Capacity*</div><div class="val" id="lCap">0</div></div>
    <div><div class="lbl">Enrolled (AWLs)*</div><div class="val" id="lAwl">0</div></div>
    <div><div class="lbl">Open Spots</div><div class="val" id="lOpen">0</div></div>
    <div><div class="lbl">Utilization*</div><div class="val" id="lUtil">0%</div></div>
  </div>
  <table id="levelTable"></table>
</section>

<div class="note">
  Source: iClass Pro public portal (<b>__ACCOUNT__</b>) · pulled __DATE__. Openings &amp;
  future openings are published live by iClass Pro. <b>*Capacity, Enrolled (AWLs) and
  Utilization are derived</b> — iClass Pro does not publish them: Private=1, Semi-Private=2
  (both confirmed by observed max openings), Group=max openings seen for that level.
  Enrolled = Capacity − Openings. Utilization colors: green ≥75%, amber 50–74%, red &lt;50%.
</div>

<script>
const META = __META__;
const DATA = __DATA__;
const LEVEL_ORDER = __LEVELS__;
const DAY_ORDER = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const DAY_INITIAL = {Sunday:"S",Monday:"M",Tuesday:"T",Wednesday:"W",Thursday:"T",Friday:"F",Saturday:"S"};
let CLASSES = [];
let LOCATION_NAME = "";

const sum = (arr,f)=>arr.reduce((a,c)=>a+f(c),0);
function timeToMinutes(t){const m=t.match(/(\d+):(\d+)\s*(AM|PM)/i);if(!m)return 0;let h=+m[1]%12;if(/PM/i.test(m[3]))h+=12;return h*60+ +m[2];}
function heatColor(n){if(n===0)return"var(--h-none)";if(n<=2)return"var(--h1)";if(n<=4)return"var(--h2)";if(n<=6)return"var(--h3)";return"var(--h4)";}
function utilColor(u){return u>=75?"var(--green)":u>=50?"var(--amber)":"var(--red)";}

function renderLevel(){
  const byLevel={};
  CLASSES.forEach(c=>{const b=byLevel[c.level]||(byLevel[c.level]={cap:0,awls:0,open:0,fut:0});
    b.cap+=c.capacity;b.awls+=c.awls;b.open+=c.openings;b.fut+=c.futureOpenings;});
  let g={cap:0,awls:0,open:0,fut:0},rows="";
  const ordered=LEVEL_ORDER.filter(l=>byLevel[l]).concat(Object.keys(byLevel).filter(l=>!LEVEL_ORDER.includes(l)));
  ordered.forEach(l=>{const b=byLevel[l];g.cap+=b.cap;g.awls+=b.awls;g.open+=b.open;g.fut+=b.fut;
    const util=b.cap?(b.awls/b.cap*100):0;
    rows+=`<tr><td>${l}</td><td class="r">${b.cap}</td>
      <td class="r" style="color:var(--red)">${b.awls}</td>
      <td class="r" style="color:var(--amber)">${b.fut}</td>
      <td class="r">${b.open}</td>
      <td class="r" style="color:${utilColor(util)};font-weight:600">${util.toFixed(1)}%</td></tr>`;});
  const gUtil=g.cap?(g.awls/g.cap*100):0;
  rows+=`<tr style="font-weight:700;background:var(--panel2)"><td>Grand Total</td>
    <td class="r">${g.cap}</td><td class="r">${g.awls}</td><td class="r">${g.fut}</td>
    <td class="r">${g.open}</td><td class="r">${gUtil.toFixed(1)}%</td></tr>`;
  document.getElementById("levelTable").innerHTML=
    `<tr><th>Level</th><th class="r">Total Capacity*</th><th class="r">Enrolled (AWLs)*</th>
     <th class="r">Future Openings</th><th class="r">Open Spots</th><th class="r">Utilization*</th></tr>`+rows;
  document.getElementById("lCap").textContent=g.cap;
  document.getElementById("lAwl").textContent=g.awls;
  document.getElementById("lOpen").textContent=g.open;
  document.getElementById("lUtil").textContent=gUtil.toFixed(1)+"%";
}

function renderGrid(){
  const day=document.getElementById("fDay").value, lvl=document.getElementById("fLevel").value;
  const data=CLASSES.filter(c=>(!day||c.day===day)&&(!lvl||c.level===lvl));
  const times=[...new Set(data.map(c=>c.time))].filter(Boolean).sort((a,b)=>timeToMinutes(a)-timeToMinutes(b));
  const cols=lvl?[lvl]:LEVEL_ORDER.filter(l=>CLASSES.some(c=>c.level===l));
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

function renderInst(){
  const sortBy=document.getElementById("iSort").value, map={};
  CLASSES.forEach(c=>{if(!c.instructor)return;
    const i=map[c.instructor]||(map[c.instructor]={name:c.instructor,total:0,days:new Set(),levels:{}});
    i.total++;i.days.add(c.day);i.levels[c.level]=(i.levels[c.level]||0)+1;});
  let list=Object.values(map).map(i=>{const top=Math.max(...Object.values(i.levels));
    i.spec=i.total?Math.round(top/i.total*100):0;return i;});
  list.sort((a,b)=>sortBy==="name"?a.name.localeCompare(b.name):sortBy==="spec"?b.spec-a.spec:b.total-a.total);
  document.getElementById("iCount").textContent=list.length;
  const totClasses=sum(list,i=>i.total);
  document.getElementById("iClasses").textContent=totClasses;
  document.getElementById("iAvg").textContent=list.length?Math.round(totClasses/list.length):0;
  let html=`<tr><th></th><th>Instructor</th><th class="c">Classes</th><th>Days</th>
    <th>Levels Taught</th><th class="c">Specialization</th></tr>`;
  list.forEach((i,idx)=>{
    const days=DAY_ORDER.filter(d=>i.days.has(d)).map(d=>DAY_INITIAL[d]).join(",");
    const chips=LEVEL_ORDER.filter(l=>i.levels[l]).map(l=>`<span class="chip">${l}</span>`).join("");
    const dist=Object.entries(i.levels).sort((a,b)=>b[1]-a[1]).map(([l,n])=>`${l}: ${n} (${Math.round(n/i.total*100)}%)`).join("<br>");
    const dayCount={};CLASSES.filter(c=>c.instructor===i.name).forEach(c=>dayCount[c.day]=(dayCount[c.day]||0)+1);
    const peakDay=Object.entries(dayCount).sort((a,b)=>b[1]-a[1])[0];
    html+=`<tr><td class="exp" onclick="toggle(${idx})">▾</td><td><b>${i.name}</b></td>
      <td class="c">${i.total}</td><td>${days}</td><td>${chips}</td><td class="c">${i.spec}%</td></tr>
      <tr id="d${idx}" style="display:none"><td></td><td colspan="5" class="detail">
        <b>Level Distribution:</b><br>${dist}<br><br>
        <b>Peak Day:</b> ${peakDay?peakDay[0]+" ("+peakDay[1]+" classes)":"—"}</td></tr>`;});
  document.getElementById("instTable").innerHTML=html;
}
function toggle(i){const r=document.getElementById("d"+i);r.style.display=r.style.display==="none"?"":"none";}

function initFilters(){
  const fd=document.getElementById("fDay");fd.innerHTML='<option value="">All Days</option>';
  DAY_ORDER.filter(d=>CLASSES.some(c=>c.day===d)).forEach(d=>fd.add(new Option(d,d)));
  const fl=document.getElementById("fLevel");fl.innerHTML='<option value="">All Levels</option>';
  LEVEL_ORDER.filter(l=>CLASSES.some(c=>c.level===l)).forEach(l=>fl.add(new Option(l,l)));
  fd.onchange=fl.onchange=renderGrid;
}
function setLocation(name){
  LOCATION_NAME=name;CLASSES=DATA[name]||[];
  document.getElementById("locLine").textContent=`Location: ${name} · ${CLASSES.length} total classes · ${META.date}`;
  initFilters();renderGrid();renderInst();renderLevel();
}
function exportCSV(){
  const hdr=["Day","Time","Class Level","Type","Capacity*","Openings","Future Openings","Enrolled(AWLs)*","Instructor"];
  const rows=CLASSES.map(c=>[c.day,c.time,c.level,c.type,c.capacity,c.openings,c.futureOpenings,c.awls,c.instructor]);
  const esc=v=>{v=String(v==null?"":v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
  const csv=[hdr.join(",")].concat(rows.map(r=>r.map(esc).join(","))).join("\n");
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));
  a.download=(META.account+"_"+LOCATION_NAME+"_"+META.date+".csv").replace(/[^a-z0-9_.-]+/gi,"_");
  a.click();
}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".view").forEach(x=>x.classList.remove("active"));
  t.classList.add("active");document.getElementById(t.dataset.v).classList.add("active");});
const ls=document.getElementById("locSel");
Object.keys(DATA).forEach(n=>ls.add(new Option(n,n)));
ls.onchange=e=>setLocation(e.target.value);
document.getElementById("csvBtn").onclick=exportCSV;
setLocation(Object.keys(DATA)[0]);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("account")
    ap.add_argument("--title", default=None)
    ap.add_argument("--date", default="")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()

    locs = api(a.account, "/locations")["data"]
    title = a.title or a.account
    data = {}
    for i, loc in enumerate(locs):
        classes = fetch_all(a.account, None if i == 0 else loc["id"])
        label = "%s, %s" % (loc["name"].title(), loc.get("state", ""))
        recs = build_records(classes)
        data[label] = recs
        print("%-24s %d classes -> %d schedule rows" % (label, len(classes), len(recs)))

    levels = LEVEL_ORDER + sorted({r["level"] for recs in data.values() for r in recs
                                   if r["level"] not in LEVEL_ORDER})
    meta = {"account": a.account, "title": title, "date": a.date or "(undated)"}
    html = (TEMPLATE
            .replace("__TITLE__", title)
            .replace("__ACCOUNT__", a.account)
            .replace("__DATE__", a.date or "(undated)")
            .replace("__META__", json.dumps(meta))
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__LEVELS__", json.dumps(levels)))
    outfn = os.path.join(a.out, a.account + ".html")
    with open(outfn, "w") as f:
        f.write(html)
    print("wrote", outfn, "(%d KB)" % (len(html) // 1024))


if __name__ == "__main__":
    main()
