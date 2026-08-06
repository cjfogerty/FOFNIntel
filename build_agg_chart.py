#!/usr/bin/env python3
"""
Rebuilds the homepage's "Total Enrollment Over Time" aggregate chart data
(the <script id="fossAggEnrollData"> JSON blob in index.html) from every
location's history/<slug>.json.

This blob is NOT derived automatically by update_index.py (which only keeps
each location's own card + per-location chart store in sync) -- it has to be
recomputed explicitly, or it silently goes stale after every weekly pull
(caught 2026-08-05: the chart was still frozen on the 2026-07-29 point after
several subsequent pulls). Wire this into the end of update_all_from_api.py's
run so it can't be forgotten again.

Usage:
    python3 build_agg_chart.py [index.html]
"""
import glob
import json
import os
import re
import sys

from append_history import SESSIONS


def build_series(repo):
    # (session, date) -> {enrolled, capacity, locations}
    buckets = {}
    for path in sorted(glob.glob(os.path.join(repo, 'history', '*.json'))):
        try:
            with open(path, encoding='utf-8') as f:
                hist = json.load(f)
        except (OSError, ValueError):
            continue
        for snap in hist.get('snapshots', []):
            session = snap.get('session')
            date = snap.get('date')
            totals = snap.get('totals') or {}
            enrolled = totals.get('enrolled')
            capacity = totals.get('capacity')
            if not session or not date or enrolled is None or capacity is None:
                continue
            if enrolled == 0 and capacity == 0:
                continue  # failed/empty pull for this location that day -- don't count it
            key = (session, date)
            b = buckets.setdefault(key, {'enrolled': 0, 'capacity': 0, 'locations': 0})
            b['enrolled'] += enrolled
            b['capacity'] += capacity
            b['locations'] += 1

    series = {}
    for (session, date), b in buckets.items():
        util = round(b['enrolled'] / b['capacity'] * 100, 1) if b['capacity'] else 0.0
        series.setdefault(session, []).append({
            'date': date, 'enrolled': b['enrolled'], 'capacity': b['capacity'],
            'util': util, 'locations': b['locations'],
        })
    for pts in series.values():
        pts.sort(key=lambda p: p['date'])
    return series


def update_index_html(index_path, series):
    with open(index_path, encoding='utf-8') as f:
        html = f.read()

    payload = json.dumps({'sessions': SESSIONS, 'series': series}, separators=(',', ':'))
    new_tag = '<script id="fossAggEnrollData" type="application/json">%s</script>' % payload

    new_html, n = re.subn(
        r'<script id="fossAggEnrollData" type="application/json">.*?</script>',
        lambda m: new_tag, html, count=1, flags=re.S)
    if n == 0:
        return False, 'fossAggEnrollData tag not found in %s' % index_path

    if new_html == html:
        return True, 'unchanged'
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(new_html)
    return True, 'updated'


def main():
    repo = os.path.dirname(os.path.abspath(__file__))
    index_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(repo, 'index.html')
    series = build_series(repo)
    ok, msg = update_index_html(index_path, series)
    for name, pts in series.items():
        last = pts[-1]
        print('  %-14s %d points, latest %s: %d enrolled / %d capacity (%d locs)'
              % (name, len(pts), last['date'], last['enrolled'], last['capacity'], last['locations']))
    print(('OK  ' if ok else 'ERR ') + msg)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
