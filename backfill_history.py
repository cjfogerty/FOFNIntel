#!/usr/bin/env python3
"""
Backfill enrollment history for FOFNIntel dashboards from git history.

Walks every commit that touched <slug>.html, pulls the embedded RAW_DATA
block out of each version, and builds history/<slug>.json (canonical) plus
history/<slug>.js (script-src loadable: `const ENROLLMENT_HISTORY = {...};`).

One snapshot per extraction DATE (later commits on the same date win, so
reverts like "go back to April 16th pull" collapse cleanly).

Usage:
    python3 backfill_history.py <slug> [<slug> ...]      # specific dashboards
    python3 backfill_history.py --all                    # all 16
    python3 backfill_history.py --all
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, date

REPO = os.path.dirname(os.path.abspath(__file__))

SLUGS = {
    'blaine': 'Blaine, MN', 'chanhassen': 'Chanhassen, MN', 'glenview': 'Glenview, IL',
    'highland_park': 'Highland Park, IL', 'lakeview': 'Lakeview, IL', 'libertyville': 'Libertyville, IL',
    'maple_grove': 'Maple Grove, MN', 'niles': 'Niles, IL', 'northglenn': 'Northglenn, CO',
    'ofallon': "O'Fallon, MO", 'richfield': 'Richfield/Edina, MN', 'stlouispark': 'St. Louis Park, MN',
    'sun_prairie': 'Sun Prairie, WI', 'western_springs': 'Western Springs, IL',
    'westminster': 'Westminster, CO', 'woodbury': 'Woodbury, MN',
    'south_barrington': 'South Barrington, IL',
}

# Session calendar. catalog_from = the date this session's catalog became the
# one being extracted (enrollment-open date). A snapshot belongs to the
# session with the latest catalog_from <= extraction date.
SESSIONS = [
    {"name": "Spring 2026", "start": "2026-03-16", "end": "2026-06-14", "catalog_from": "2026-01-01"},
    {"name": "Summer 2026", "start": "2026-06-15", "end": "2026-08-30", "catalog_from": "2026-05-12"},
]

CATEGORY_MAP = {
    "Preview": "Previews",  # first: prefix-matched before Little/Big/etc.
    "Backfloat Baby": "Backfloat Baby", "Little": "Littles", "Middle": "Middles",
    "Big": "Bigs", "10+": "10+", "Adult": "Adults", "Private Lesson": "Privates",
}

def get_category(level):
    for prefix, cat in CATEGORY_MAP.items():
        if level.startswith(prefix):
            return cat
    return "Other"

def time_to_slot(t):
    t = t.strip()
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(t, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return t

def git(*args):
    return subprocess.run(['git', '-C', REPO] + list(args),
                          capture_output=True, text=True, check=True).stdout

def parse_extraction_dt(html, commit_iso):
    """Best extraction timestamp: extraction-time div, else commit date.
    (Title dates are unreliable — old dashboards never refreshed them.)"""
    m = re.search(r'Data extracted:\s*([^<]+)<', html)
    if m:
        for fmt in ("%B %d, %Y at %I:%M %p", "%b %d, %Y at %I:%M %p"):
            try:
                return datetime.strptime(m.group(1).strip(), fmt)
            except ValueError:
                continue
    return datetime.fromisoformat(commit_iso).replace(tzinfo=None)

def parse_session(html, dt):
    m = re.search(r'session-info"><strong>([A-Za-z]+ \d{4}) Session', html)
    if m:
        return m.group(1)
    d, best = dt.date().isoformat(), None
    for s in SESSIONS:
        if s["catalog_from"] <= d:
            best = s["name"]
    return best or SESSIONS[0]["name"]

def extract_raw_data(html):
    m = re.search(r'const RAW_DATA = (\[.*?\]);', html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

def build_history(slug):
    # No --follow: these dashboards were created as copies of each other, and
    # git rename-detection would falsely chain one location's history into
    # another's. Exact-path history only.
    log = git('log', '--format=%H %cI', '--', f'{slug}.html')
    commits = [line.split() for line in log.strip().splitlines() if line.strip()]
    commits.reverse()  # oldest -> newest so same-date later commits win

    by_date = {}
    for sha, commit_iso in commits:
        try:
            html = git('show', f'{sha}:{slug}.html')
        except subprocess.CalledProcessError:
            continue
        raw = extract_raw_data(html)
        if not raw:
            continue
        dt = parse_extraction_dt(html, commit_iso)
        session = parse_session(html, dt)

        slots, enrolled, capacity = {}, 0, 0
        for r in raw:
            try:
                e, c = int(r.get("Enrolled", 0) or 0), int(r.get("Total Capacity", 0) or 0)
            except (TypeError, ValueError):
                continue
            key = f'{r.get("Day","")}|{time_to_slot(r.get("Time",""))}|{r.get("Class Level","")}'
            if key in slots:  # duplicate slot rows aggregate
                slots[key][0] += e
                slots[key][1] += c
            else:
                slots[key] = [e, c]
            enrolled += e
            capacity += c

        util = round(enrolled / capacity * 100, 1) if capacity else 0.0
        by_date[dt.date().isoformat()] = {
            "date": dt.date().isoformat(),
            "ts": dt.strftime("%Y-%m-%dT%H:%M"),
            "label": dt.strftime("%b %-d"),
            "session": session,
            "totals": {"enrolled": enrolled, "capacity": capacity, "utilization": util},
            "slots": slots,
        }

    # Collapse consecutive identical snapshots (reverts like "go back to
    # April 16th pull" re-commit the same data on a later date).
    snapshots = []
    for d in sorted(by_date):
        s = by_date[d]
        if snapshots and snapshots[-1]["slots"] == s["slots"]:
            continue
        snapshots.append(s)
    return {
        "location": SLUGS.get(slug, slug),
        "slug": slug,
        "sessions": SESSIONS,
        "snapshots": snapshots,
    }

def write_history(hist, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    slug = hist["slug"]
    json_path = os.path.join(out_dir, f'{slug}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
    js_path = os.path.join(out_dir, f'{slug}.js')
    with open(js_path, 'w', encoding='utf-8') as f:
        f.write('const ENROLLMENT_HISTORY = ')
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    return json_path, js_path

def main():
    args = [a for a in sys.argv[1:]]
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'history')
    if '--out' in args:
        i = args.index('--out')
        out_dir = args[i + 1]
        del args[i:i + 2]
    slugs = list(SLUGS) if '--all' in args else [a for a in args if a in SLUGS]
    if not slugs:
        print(__doc__)
        sys.exit(1)
    for slug in slugs:
        hist = build_history(slug)
        jp, _ = write_history(hist, out_dir)
        sess = {}
        for s in hist["snapshots"]:
            sess.setdefault(s["session"], []).append(s["date"])
        print(f'{slug}: {len(hist["snapshots"])} snapshots -> {jp}')
        for name, dates in sess.items():
            print(f'    {name}: {len(dates)} ({dates[0]} .. {dates[-1]})')

if __name__ == '__main__':
    main()
