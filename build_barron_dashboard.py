#!/usr/bin/env python3
"""
Build a FOSS-dashboard-styled Schedule & Utilization dashboard for a Barron
location from the Jackrabbit Class parent-portal JSON — see barron_extract.py,
which logs in and pulls this JSON on a schedule (Barron has no public API like
the iClass Pro competitors — see build_iclass_dashboard.py).

Visually matches the FOFNIntel FOSS-location dashboards (light theme, card
layout, filter row, Chart.js bars) while retaining the extra fields Barron
publishes that FOSS's own dashboards don't need: wait-list vs open status,
instructor names, and — for multi-program locations like Ballwin — a
Program (Swim/Gymnastics/Dance/Ninja/...) filter alongside Day/Level/Status/
Instructor.

Source: one JSON array of class objects from GetClassesForEnroll, each with
instructor name, category1/2/3, per-weekday meeting flags (dayMon..daySun) and
per-weekday capacity (monOpen..sunOpen -- constant across a class's single
meeting day; NOT open-spot counts despite the name), "openings" (numeric string
of current open spots, or the literal "Wait List" when the class is full), and
"isWaitlist" (bool).

Usage: python3 build_barron_dashboard.py barron_raw.json --date 2026-07-30
"""
import re, json, argparse, os

LEVEL_ORDER = [
    "Parent & Tot I", "Parent & Tot II", "Little Junior", "Kinder Junior",
    "Beginner", "Advanced Junior", "Intermediate", "Intermediate II",
    "Advanced", "Elite Junior", "Elite Junior II", "SwimAbilities",
]
DAY_FIELDS = [
    ("dayMon", "monOpen", "Monday"), ("dayTue", "tueOpen", "Tuesday"),
    ("dayWed", "wedOpen", "Wednesday"), ("dayThu", "thuOpen", "Thursday"),
    ("dayFri", "friOpen", "Friday"), ("daySat", "satOpen", "Saturday"),
    ("daySun", "sunOpen", "Sunday"),
]
# category1 comes through with odd variants for edge-case classes (bring-a-friend,
# summer camps, swim meets) -- fold those into their obvious parent program so the
# Ballwin filter shows the 4 real programs (Swim/Gymnastics/Dance/Ninja) instead of
# 7 near-duplicates.
PROGRAM_MAP = {
    "Swim Meet": "Swim",
    "Swim - Private Lesson": "Swim",
    "Swim - Bring A Friend": "Swim",
    "Gym - Summer Camps": "Gymnastics",
}
LEVEL_PREFIX_RE = re.compile(r"^(Swim|Gym|Dance|Ninja)\s*-\s*", re.I)


def to12h(t):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*", str(t or ""))
    return (m.group(1) + ":" + m.group(2) + " " + m.group(3).upper()) if m else ""


def program_of(c):
    raw = (c.get("category1") or "").strip()
    return PROGRAM_MAP.get(raw, raw or "Other")


def level_of(c):
    # Ballwin's session field is program-prefixed ("Dance Classes", "Swim Activities")
    # rather than O'Fallon/South County's plain "Classes"/"Special Activities".
    if (c.get("session") or "").endswith("Activities"):
        return "Special Event"
    lvl = LEVEL_PREFIX_RE.sub("", c.get("category3") or "").strip()
    lvl = lvl.replace(" and ", " & ")
    return lvl or "Other"


def build_records(classes):
    recs = []
    for c in classes:
        day = next((full for flag, _, full in DAY_FIELDS if c.get(flag)), "")
        cap = next((c.get(openf) or 0 for flag, openf, _ in DAY_FIELDS if c.get(flag)), 0)
        cap = int(cap)
        is_wait = bool(c.get("isWaitlist"))
        try:
            openings = 0 if is_wait else int(c.get("openings") or 0)
        except (TypeError, ValueError):
            openings = 0
        enrolled = max(cap - openings, 0) if cap else 0
        recs.append({
            "name": (c.get("className") or "").strip(),
            "program": program_of(c),
            "level": level_of(c),
            "capacity": cap,
            "openings": openings,
            "awls": enrolled,
            "status": "Wait List" if is_wait else "Open",
            "day": day,
            "time": to12h(c.get("startTime")),
            "instructor": c.get("instructor") or "",
            "sessionType": c.get("session") or "",
            "tuition": c.get("tuitionFee"),
            "ageRange": c.get("ageRange") or "",
        })
    return recs


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ — __LOCATION__ Class Catalog</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1"></script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;background:#f5f7fa;padding:20px;color:#2d3748}
  .dashboard{max-width:1600px;margin:0 auto}
  a.back-home{display:inline-block;margin-bottom:12px;color:#2b6cb0;text-decoration:none;font-size:14px;font-weight:600}
  a.back-home:hover{text-decoration:underline}
  .header{background:#fff;padding:25px 30px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.05);margin-bottom:20px}
  h1{font-size:24px;font-weight:600;color:#1a202c;margin-bottom:6px}
  .pull-time{font-size:13px;color:#718096;margin-bottom:15px}
  .filters{display:flex;gap:15px;flex-wrap:wrap;margin-top:15px}
  .filter-group{display:flex;flex-direction:column;gap:5px}
  .filter-group label{font-size:11px;font-weight:600;color:#4a5568;text-transform:uppercase;letter-spacing:.5px}
  select{padding:8px 12px;border:2px solid #e2e8f0;border-radius:6px;font-size:13px;background:#fff;cursor:pointer;min-width:160px}
  select:hover,select:focus{outline:none;border-color:#5b4a9f}
  .slider-group{display:flex;flex-direction:column;gap:5px;min-width:220px}
  .slider-row{display:flex;align-items:center;gap:10px}
  .slider-row input[type=range]{flex:1;accent-color:#5b4a9f}
  .slider-row .pct-val{font-size:13px;font-weight:700;color:#5b4a9f;min-width:38px;text-align:right}
  .stat-row{display:grid;grid-template-columns:repeat(6,1fr);gap:16px;margin-bottom:20px}
  .stat-card{background:#fff;padding:18px 16px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.05);text-align:center}
  .stat-card .val{font-size:26px;font-weight:700;color:#5b4a9f}
  .stat-card .val.wait{color:#c05621}
  .stat-card .val.rev{color:#2f855a}
  .stat-card .sub{font-size:12px;color:#a0aec0;margin-top:2px}
  .stat-card .lbl{font-size:11px;font-weight:600;color:#718096;text-transform:uppercase;letter-spacing:.5px;margin-top:6px}
  .chart-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:20px;margin-bottom:20px}
  .chart-card{background:#fff;padding:25px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.05)}
  .chart-card.full-width{grid-column:1/-1}
  .chart-title{font-size:15px;font-weight:600;color:#2d3748;margin-bottom:20px;padding-bottom:10px;border-bottom:2px solid #e2e8f0}
  .chart-wrap{position:relative;height:340px}
  .table-card{background:#fff;padding:25px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,.05);margin-bottom:20px;overflow-x:auto}
  table{width:100%;border-collapse:collapse;font-size:13px}
  thead th{background:#f7fafc;padding:10px 8px;text-align:left;font-weight:600;border-bottom:2px solid #e2e8f0;white-space:nowrap}
  th.c,td.c{text-align:center}
  th.r,td.r{text-align:right}
  tbody td{padding:8px;border-bottom:1px solid #e2e8f0}
  tbody tr:hover{background:#f7fafc}
  .util-cell{font-weight:600;padding:5px 10px;border-radius:4px;display:inline-block;min-width:56px;text-align:center}
  .util-0-20{background:#e6f4ff;color:#0066cc}
  .util-20-40{background:#cce5ff;color:#0052a3}
  .util-40-60{background:#99ccff;color:#003d7a}
  .util-60-80{background:#4d94ff;color:#fff}
  .util-80-100{background:#0066cc;color:#fff}
  .util-100{background:#003d7a;color:#fff}
  .total-row{background:#f7fafc;font-weight:700}
  .pill{display:inline-block;border-radius:12px;padding:2px 10px;font-size:11px;font-weight:700}
  .pill.open{background:#e6fffa;color:#22543d}
  .pill.wait{background:#fff5f5;color:#c53030}
  .badge-program{display:inline-block;background:#edf2f7;color:#4a5568;border-radius:10px;padding:1px 8px;font-size:11px;font-weight:600}
  .heat-none{background:#edf2f7}.heat-1{background:#c6e0ff}.heat-2{background:#8fc1ff}.heat-3{background:#4d94ff;color:#fff}.heat-4{background:#0066cc;color:#fff}
  .note{max-width:1000px;margin:14px auto 0;font-size:12px;color:#718096;line-height:1.5}
  @media (max-width:1300px){.stat-row{grid-template-columns:repeat(3,1fr)}}
  @media (max-width:1100px){.chart-grid{grid-template-columns:1fr}}
  @media (max-width:700px){.stat-row{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
<div class="dashboard">
  <a href="index.html" class="back-home">&larr; FOFNIntel hub</a>
  <div class="header">
    <h1>🏊 __TITLE__ — __LOCATION__</h1>
    <div class="pull-time">Data pulled __DATE__ &middot; <span id="hCount">0</span> classes</div>
    <div class="filters">
      <div class="filter-group"><label>Program</label><select id="fProgram"><option value="">All Programs</option></select></div>
      <div class="filter-group"><label>Day of Week</label><select id="fDay"><option value="">All Days</option></select></div>
      <div class="filter-group"><label>Level</label><select id="fLevel"><option value="">All Levels</option></select></div>
      <div class="filter-group"><label>Status</label><select id="fStatus"><option value="">Open + Wait List</option><option value="Open">Open Only</option><option value="Wait List">Wait List Only</option></select></div>
      <div class="filter-group"><label>Instructor</label><select id="fInstructor"><option value="">All Instructors</option></select></div>
      <div class="slider-group">
        <label>% of Revenue Collected</label>
        <div class="slider-row">
          <input type="range" id="fCollectPct" min="75" max="100" step="1" value="100">
          <span class="pct-val" id="fCollectPctVal">100%</span>
        </div>
      </div>
    </div>
  </div>

  <div class="stat-row">
    <div class="stat-card"><div class="val" id="sClasses">0</div><div class="lbl">Classes Shown</div></div>
    <div class="stat-card"><div class="val" id="sEnroll">0</div><div class="lbl">Est. Enrollment</div><div class="sub" id="sEnrollSub">of 0 capacity*</div></div>
    <div class="stat-card"><div class="val" id="sUtil">0%</div><div class="lbl">Utilization</div><div class="sub" id="sUtilSub">0 of 0 capacity*</div></div>
    <div class="stat-card"><div class="val rev" id="sRevenue">$0</div><div class="lbl">Est. Weekly Revenue</div><div class="sub" id="sRevenueSub">at 100% collected</div></div>
    <div class="stat-card"><div class="val" id="sOpen">0</div><div class="lbl">Open Spots Right Now</div></div>
    <div class="stat-card"><div class="val wait" id="sWait">0</div><div class="lbl">Wait-Listed Classes</div></div>
  </div>

  <div class="chart-grid">
    <div class="chart-card">
      <div class="chart-title">Utilization by Day of Week</div>
      <div class="chart-wrap"><canvas id="dayChart"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Utilization by Level</div>
      <div class="chart-wrap"><canvas id="levelChart"></canvas></div>
    </div>
    <div class="chart-card full-width">
      <div class="chart-title">Est. Revenue by Day of Week</div>
      <div class="chart-wrap"><canvas id="revenueChart"></canvas></div>
    </div>
  </div>

  <div class="table-card">
    <div class="chart-title">Summary by Level</div>
    <table id="levelTable"></table>
  </div>

  <div class="table-card">
    <div class="chart-title">Instructors</div>
    <table id="instTable"></table>
  </div>

  <div class="table-card">
    <div class="chart-title">Full Class List</div>
    <table id="rosterTable"></table>
  </div>

  <div class="note">
    Source: __TITLE__ Jackrabbit Class parent portal (authenticated) &middot; pulled __DATE__.
    <b>*Capacity</b> is the class's published per-day-of-week slot count (Jackrabbit doesn't
    label it "capacity" directly, but it holds constant whether or not the class is full).
    Enrolled = Capacity − Open Spots (treated as 0 once a class is wait-listed).
    <b>Est. Revenue</b> treats each class's listed tuition as a monthly recurring fee
    per enrolled student (Barron bills weekly lessons monthly), derives a per-lesson
    rate by dividing by 4.33 weeks/month, then multiplies by enrollment to estimate
    the revenue generated each time that class meets — summed by day of week for one
    week. One-time items (swim meets, special activities) are excluded since their
    listed price isn't a recurring weekly fee. The "% of Revenue Collected" slider
    scales every revenue figure down to account for discounts, scholarships, and
    multi-class family pricing that the listed tuition doesn't reflect.
    All filters above apply across every chart and table on this page.
  </div>
</div>

<script>
const META = __META__;
const DATA = __DATA__;
const LEVEL_ORDER = __LEVELS__;
const DAY_ORDER = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
const WEEKS_PER_MONTH = 4.33;
let dayChart, levelChart, revenueChart;

const sum = (arr,f)=>arr.reduce((a,c)=>a+f(c),0);
function utilClass(u){
  if(u>=100)return"util-100";if(u>=80)return"util-80-100";if(u>=60)return"util-60-80";
  if(u>=40)return"util-40-60";if(u>=20)return"util-20-40";return"util-0-20";
}
function heatClass(n){if(!n)return"heat-none";if(n<=2)return"heat-1";if(n<=4)return"heat-2";if(n<=6)return"heat-3";return"heat-4";}
function money(n){return "$"+Math.round(n).toLocaleString();}
// Only "Classes" session-type rows carry a recurring weekly tuition; swim meets /
// special activities are one-time fees and would overstate a "weekly" estimate.
function revenueByDay(data, pct){
  const byDay={}; DAY_ORDER.forEach(d=>byDay[d]=0); let total=0;
  data.forEach(c=>{
    if(!/Classes$/.test(c.sessionType||"")||c.tuition==null||!c.awls||!c.day)return;
    const occRevenue=(c.tuition/WEEKS_PER_MONTH)*c.awls*(pct/100);
    byDay[c.day]+=occRevenue; total+=occRevenue;
  });
  return {byDay,total};
}

function filtered(){
  const program=document.getElementById("fProgram").value;
  const day=document.getElementById("fDay").value;
  const level=document.getElementById("fLevel").value;
  const status=document.getElementById("fStatus").value;
  const instructor=document.getElementById("fInstructor").value;
  return DATA.filter(c=>
    (!program||c.program===program) &&
    (!day||c.day===day) &&
    (!level||c.level===level) &&
    (!status||c.status===status) &&
    (!instructor||c.instructor===instructor));
}

function renderStats(data){
  document.getElementById("hCount").textContent=DATA.length;
  document.getElementById("sClasses").textContent=data.length;
  const cap=sum(data,c=>c.capacity), awls=sum(data,c=>c.awls), open=sum(data,c=>c.openings);
  const wait=data.filter(c=>c.status==="Wait List").length;
  const util=cap?(awls/cap*100):0;
  document.getElementById("sEnroll").textContent=awls;
  document.getElementById("sEnrollSub").textContent=`of ${cap} capacity*`;
  document.getElementById("sUtil").textContent=util.toFixed(1)+"%";
  document.getElementById("sUtilSub").textContent=`${awls} of ${cap} capacity*`;
  document.getElementById("sOpen").textContent=open;
  document.getElementById("sWait").textContent=wait;
}

function renderRevenue(data){
  const pct=+document.getElementById("fCollectPct").value;
  document.getElementById("fCollectPctVal").textContent=pct+"%";
  const {byDay,total}=revenueByDay(data,pct);
  document.getElementById("sRevenue").textContent=money(total);
  document.getElementById("sRevenueSub").textContent=`at ${pct}% collected (${money(revenueByDay(data,100).total)} at 100%)`;
  const labels=DAY_ORDER.filter(d=>byDay[d]>0);
  const vals=labels.map(d=>+byDay[d].toFixed(2));
  if(revenueChart)revenueChart.destroy();
  revenueChart=new Chart(document.getElementById("revenueChart"),{
    type:"bar",
    data:{labels,datasets:[{label:"Est. Revenue",data:vals,backgroundColor:"#2f855a"}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{tooltip:{callbacks:{label:ctx=>money(ctx.parsed.y)}}},
      scales:{y:{title:{display:true,text:"Est. Revenue ($)"},ticks:{callback:v=>money(v)}}}}
  });
}

function renderDayChart(data){
  const byDay={};
  DAY_ORDER.forEach(d=>byDay[d]={cap:0,awls:0});
  data.forEach(c=>{if(!c.day)return;byDay[c.day].cap+=c.capacity;byDay[c.day].awls+=c.awls;});
  const labels=DAY_ORDER.filter(d=>byDay[d].cap>0);
  const util=labels.map(d=>byDay[d].cap?+(byDay[d].awls/byDay[d].cap*100).toFixed(1):0);
  const cap=labels.map(d=>byDay[d].cap), awls=labels.map(d=>byDay[d].awls);
  if(dayChart)dayChart.destroy();
  dayChart=new Chart(document.getElementById("dayChart"),{
    data:{labels,datasets:[
      {type:"bar",label:"Utilization %",data:util,backgroundColor:"#5b4a9f",yAxisID:"y"},
      {type:"line",label:"Capacity*",data:cap,borderColor:"#a0c4ff",backgroundColor:"#a0c4ff",yAxisID:"y1",tension:.3},
      {type:"line",label:"Enrolled (est.)*",data:awls,borderColor:"#2b6cb0",backgroundColor:"#2b6cb0",yAxisID:"y1",tension:.3},
    ]},
    options:{responsive:true,maintainAspectRatio:false,
      scales:{y:{position:"left",title:{display:true,text:"Utilization %"},min:0},
               y1:{position:"right",title:{display:true,text:"Classes / Capacity"},grid:{drawOnChartArea:false}}}}
  });
}

function renderLevelChart(data){
  const byLevel={};
  data.forEach(c=>{const b=byLevel[c.level]||(byLevel[c.level]={cap:0,awls:0});b.cap+=c.capacity;b.awls+=c.awls;});
  const labels=LEVEL_ORDER.filter(l=>byLevel[l]).concat(Object.keys(byLevel).filter(l=>!LEVEL_ORDER.includes(l)));
  const util=labels.map(l=>byLevel[l].cap?+(byLevel[l].awls/byLevel[l].cap*100).toFixed(1):0);
  if(levelChart)levelChart.destroy();
  levelChart=new Chart(document.getElementById("levelChart"),{
    type:"bar",
    data:{labels,datasets:[{label:"Utilization %",data:util,backgroundColor:"#5b4a9f"}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:"y",
      scales:{x:{min:0,max:100,title:{display:true,text:"Utilization %"}}}}
  });
}

function renderLevelTable(data){
  const byLevel={};
  data.forEach(c=>{const b=byLevel[c.level]||(byLevel[c.level]={cap:0,awls:0,open:0,wait:0});
    b.cap+=c.capacity;b.awls+=c.awls;b.open+=c.openings;if(c.status==="Wait List")b.wait++;});
  const ordered=LEVEL_ORDER.filter(l=>byLevel[l]).concat(Object.keys(byLevel).filter(l=>!LEVEL_ORDER.includes(l)));
  let g={cap:0,awls:0,open:0,wait:0},rows="";
  ordered.forEach(l=>{const b=byLevel[l];g.cap+=b.cap;g.awls+=b.awls;g.open+=b.open;g.wait+=b.wait;
    const util=b.cap?(b.awls/b.cap*100):0;
    rows+=`<tr><td>${l}</td><td class="r">${b.cap}</td><td class="r">${b.awls}</td>
      <td class="r">${b.open}</td><td class="r">${b.cap?`<span class="util-cell ${utilClass(util)}">${util.toFixed(1)}%</span>`:"—"}</td>
      <td class="r">${b.wait}</td></tr>`;});
  const gUtil=g.cap?(g.awls/g.cap*100):0;
  rows+=`<tr class="total-row"><td>Grand Total</td><td class="r">${g.cap}</td><td class="r">${g.awls}</td>
    <td class="r">${g.open}</td><td class="r"><span class="util-cell ${utilClass(gUtil)}">${gUtil.toFixed(1)}%</span></td><td class="r">${g.wait}</td></tr>`;
  document.getElementById("levelTable").innerHTML=
    `<tr><th>Level</th><th class="r">Capacity*</th><th class="r">Enrolled (est.)*</th>
     <th class="r">Open Spots</th><th class="r">Utilization*</th><th class="r">Wait-Listed</th></tr>`+rows;
}

function renderInstTable(data){
  const map={};
  data.forEach(c=>{if(!c.instructor)return;
    const i=map[c.instructor]||(map[c.instructor]={name:c.instructor,total:0,days:new Set(),levels:{},programs:new Set()});
    i.total++;if(c.day)i.days.add(c.day);i.levels[c.level]=(i.levels[c.level]||0)+1;i.programs.add(c.program);});
  const list=Object.values(map).sort((a,b)=>b.total-a.total);
  let rows="";
  list.forEach(i=>{
    const days=DAY_ORDER.filter(d=>i.days.has(d)).join(", ");
    const progs=[...i.programs].map(p=>`<span class="badge-program">${p}</span>`).join(" ");
    const levels=Object.keys(i.levels).map(l=>`${l} (${i.levels[l]})`).join(", ");
    rows+=`<tr><td><b>${i.name}</b></td><td class="c">${i.total}</td><td>${progs}</td><td>${days}</td><td>${levels}</td></tr>`;});
  document.getElementById("instTable").innerHTML=
    `<tr><th>Instructor</th><th class="c">Classes</th><th>Program(s)</th><th>Days</th><th>Levels Taught</th></tr>`+rows;
}

function renderRoster(data){
  const rows=data.map(c=>
    `<tr><td>${c.name}</td><td><span class="badge-program">${c.program}</span></td><td>${c.level}</td>
     <td>${c.instructor||"—"}</td><td>${c.day||"—"}</td><td>${c.time||"—"}</td>
     <td class="c"><span class="pill ${c.status==="Open"?"open":"wait"}">${c.status}</span></td>
     <td class="r">${c.openings}</td><td class="r">${c.capacity||"—"}</td>
     <td class="r">${c.tuition!=null?"$"+c.tuition.toFixed(2):"—"}</td></tr>`).join("");
  document.getElementById("rosterTable").innerHTML=
    `<tr><th>Class</th><th>Program</th><th>Level</th><th>Instructor</th><th>Day</th><th>Time</th>
     <th class="c">Status</th><th class="r">Spots Left</th><th class="r">Capacity*</th><th class="r">Tuition</th></tr>`+rows;
}

function renderAll(){
  const data=filtered();
  renderStats(data);renderDayChart(data);renderLevelChart(data);renderRevenue(data);
  renderLevelTable(data);renderInstTable(data);renderRoster(data);
}

function initFilters(){
  const fProgram=document.getElementById("fProgram");
  [...new Set(DATA.map(c=>c.program))].sort().forEach(p=>fProgram.add(new Option(p,p)));
  const fDay=document.getElementById("fDay");
  DAY_ORDER.filter(d=>DATA.some(c=>c.day===d)).forEach(d=>fDay.add(new Option(d,d)));
  const fLevel=document.getElementById("fLevel");
  LEVEL_ORDER.concat(Object.keys(Object.fromEntries(DATA.map(c=>[c.level,1]))).filter(l=>!LEVEL_ORDER.includes(l)))
    .filter(l=>DATA.some(c=>c.level===l)).forEach(l=>fLevel.add(new Option(l,l)));
  const fInstructor=document.getElementById("fInstructor");
  [...new Set(DATA.map(c=>c.instructor).filter(Boolean))].sort().forEach(i=>fInstructor.add(new Option(i,i)));
  [fProgram,fDay,fLevel,document.getElementById("fStatus"),fInstructor].forEach(el=>el.onchange=renderAll);
  document.getElementById("fCollectPct").oninput=()=>renderRevenue(filtered());
  // Ballwin-style multi-program locations: hide the Program filter entirely when
  // there's only one program (O'Fallon / South County are pure swim).
  if(new Set(DATA.map(c=>c.program)).size<=1){
    fProgram.closest(".filter-group").style.display="none";
  }
}

initFilters();renderAll();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--title", default="Barron Swim School")
    ap.add_argument("--location", default="O'Fallon, MO")
    ap.add_argument("--slug", default="barron_ofallon", help="output filename stem -> <slug>.html")
    ap.add_argument("--date", default="")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()

    with open(a.json_path) as f:
        classes = json.load(f)
    data = build_records(classes)
    levels = LEVEL_ORDER + sorted({r["level"] for r in data if r["level"] not in LEVEL_ORDER})
    meta = {"title": a.title, "date": a.date or "(undated)"}
    html = (TEMPLATE
            .replace("__TITLE__", a.title)
            .replace("__LOCATION__", a.location)
            .replace("__DATE__", a.date or "(undated)")
            .replace("__META__", json.dumps(meta))
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__LEVELS__", json.dumps(levels)))
    outfn = os.path.join(a.out, a.slug + ".html")
    with open(outfn, "w") as f:
        f.write(html)
    print("wrote", outfn, "(%d KB, %d classes)" % (len(html) // 1024, len(data)))


if __name__ == "__main__":
    main()
