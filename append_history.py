#!/usr/bin/env python3
"""
Append one enrollment snapshot to history/<slug>.json + history/<slug>.js.

This is the production hook: update_dashboard.py imports append_snapshot()
and calls it right after computing raw_data, so every scheduled pull grows
the history automatically. Also runnable standalone against a CSV:

    python3 append_history.py foss_api_csv_northglenn.csv northglenn \
            [--session "Summer 2026"] [--history-dir history]

Rules:
  - One snapshot per calendar date; re-running the same day overwrites it.
  - Session comes from --session, else inferred from the SESSIONS calendar
    (latest session whose enrollment-open date <= today).
  - Writes both .json (canonical) and .js (loaded by the dashboard via
    <script src="history/<slug>.js">, which works on file:// and Pages).
"""
import csv
import json
import os
import sys
from datetime import datetime

# Session calendar — extend each quarter (one line per new session).
SESSIONS = [
    {"name": "Spring 2026", "start": "2026-03-16", "end": "2026-06-14", "catalog_from": "2026-01-01"},
    {"name": "Summer 2026", "start": "2026-06-15", "end": "2026-08-30", "catalog_from": "2026-05-12"},
    {"name": "Fall 2026", "start": "2026-08-31", "end": "2026-11-29", "catalog_from": "2026-08-04"},
]

def infer_session(d=None):
    d = d or datetime.now().date().isoformat()
    best = SESSIONS[0]["name"]
    for s in SESSIONS:
        if s["catalog_from"] <= d:
            best = s["name"]
    return best

def time_to_slot(t):
    t = t.strip()
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(t, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return t

def build_snapshot(raw_data, session=None, now=None):
    """raw_data: list of dicts with Day, Time, Class Level, Enrolled, Total Capacity
    (exactly what update_dashboard.load_csv() returns)."""
    now = now or datetime.now()
    slots, enrolled, capacity = {}, 0, 0
    for r in raw_data:
        try:
            e = int(r.get("Enrolled", 0) or 0)
            c = int(r.get("Total Capacity", 0) or 0)
        except (TypeError, ValueError):
            continue
        key = f'{r.get("Day","")}|{time_to_slot(str(r.get("Time","")))}|{r.get("Class Level","")}'
        if key in slots:
            slots[key][0] += e
            slots[key][1] += c
        else:
            slots[key] = [e, c]
        enrolled += e
        capacity += c
    return {
        "date": now.date().isoformat(),
        "ts": now.strftime("%Y-%m-%dT%H:%M"),
        "label": now.strftime("%b %-d"),
        "session": session or infer_session(now.date().isoformat()),
        "totals": {"enrolled": enrolled, "capacity": capacity,
                   "utilization": round(enrolled / capacity * 100, 1) if capacity else 0.0},
        "slots": slots,
    }

def append_snapshot(slug, location, raw_data, history_dir, session=None, now=None):
    os.makedirs(history_dir, exist_ok=True)
    json_path = os.path.join(history_dir, f'{slug}.json')
    if os.path.exists(json_path):
        with open(json_path, encoding='utf-8') as f:
            hist = json.load(f)
    else:
        hist = {"location": location, "slug": slug, "sessions": SESSIONS, "snapshots": []}

    snap = build_snapshot(raw_data, session=session, now=now)
    hist["sessions"] = SESSIONS  # keep the calendar current
    # Dedup on (date, session) rather than date alone: during a session
    # transition (e.g. Aug 2026, Summer still running while Fall ramps up
    # pre-enrollment) the same calendar date can legitimately carry one
    # snapshot per in-flight session. Re-running the same day+session still
    # overwrites cleanly.
    hist["snapshots"] = [s for s in hist["snapshots"]
                          if not (s["date"] == snap["date"] and s["session"] == snap["session"])]
    hist["snapshots"].append(snap)
    hist["snapshots"].sort(key=lambda s: s["date"])

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(history_dir, f'{slug}.js'), 'w', encoding='utf-8') as f:
        f.write('const ENROLLMENT_HISTORY = ')
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    return snap

def main():
    args = sys.argv[1:]
    session = None
    history_dir = 'history'
    if '--session' in args:
        i = args.index('--session')
        session = args[i + 1]
        del args[i:i + 2]
    if '--history-dir' in args:
        i = args.index('--history-dir')
        history_dir = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    csv_path, slug = args[0], args[1]
    with open(csv_path, encoding='utf-8-sig') as f:
        raw = list(csv.DictReader(f))
    snap = append_snapshot(slug, slug, raw, history_dir, session=session)
    print(f'{slug}: snapshot {snap["date"]} ({snap["session"]}) '
          f'enrolled={snap["totals"]["enrolled"]} util={snap["totals"]["utilization"]}% '
          f'-> {history_dir}/{slug}.json')

if __name__ == '__main__':
    main()
