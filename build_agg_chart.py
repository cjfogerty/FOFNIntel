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

# The "core" cohort compares Summer vs Fall apples-to-apples: Fall keeps growing as
# new locations get added to the tracker (17 -> 34+), which skews a raw sum against
# Summer's smaller footprint. Anchor on "CORE_SEASON" (the earliest session with
# broad coverage) rather than hardcoding slugs, so the cohort is self-maintaining.
CORE_SEASON = 'Summer 2026'

# Once FOSS closes a session to new enrollment it stops returning that session's
# classes, so the last pull or two of a session can come back badly short (e.g.
# Westminster lost 23 of its 24 Sunday Summer classes between the 2026-07-16 and
# 2026-07-19 pulls -- 581 seats down to 484). Summing those raw would show a fake
# enrollment drop at the tail of every session. A pull returning less than this
# share of the prior pull's seats is treated as truncated and the prior pull's
# totals are carried forward for that date instead. Mirrors TRUNC_RATIO in
# retrofit_session_freeze.py, which freezes the per-location "final" the same way.
TRUNC_RATIO = 0.95


def _history_files(repo):
    return sorted(glob.glob(os.path.join(repo, 'history', '*.json')))


def _carry_forward_truncated(snaps):
    """Yield (session, date, enrolled, capacity) with session-close truncation
    repaired by holding the last complete pull's totals."""
    ordered = sorted(snaps, key=lambda s: (s.get('session') or '', s.get('date') or ''))
    last_good = {}  # session -> (enrolled, capacity)
    for snap in ordered:
        session, date = snap.get('session'), snap.get('date')
        totals = snap.get('totals') or {}
        enrolled, capacity = totals.get('enrolled'), totals.get('capacity')
        if not session or not date or enrolled is None or capacity is None:
            continue
        if enrolled == 0 and capacity == 0:
            continue  # failed/empty pull for this location that day -- don't count it
        prev = last_good.get(session)
        if prev and capacity < prev[1] * TRUNC_RATIO:
            yield session, date, prev[0], prev[1]
            continue
        last_good[session] = (enrolled, capacity)
        yield session, date, enrolled, capacity


def find_core_slugs(repo):
    """Slugs with at least one real (non 0/0) CORE_SEASON snapshot."""
    slugs = []
    for path in _history_files(repo):
        slug = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding='utf-8') as f:
                hist = json.load(f)
        except (OSError, ValueError):
            continue
        for snap in hist.get('snapshots', []):
            totals = snap.get('totals') or {}
            if (snap.get('session') == CORE_SEASON
                    and not (totals.get('enrolled') == 0 and totals.get('capacity') == 0)):
                slugs.append(slug)
                break
    return slugs


def build_series(repo, only_slugs=None):
    # (session, date) -> {enrolled, capacity, locations}
    buckets = {}
    for path in _history_files(repo):
        slug = os.path.splitext(os.path.basename(path))[0]
        if only_slugs is not None and slug not in only_slugs:
            continue
        try:
            with open(path, encoding='utf-8') as f:
                hist = json.load(f)
        except (OSError, ValueError):
            continue
        for session, date, enrolled, capacity in _carry_forward_truncated(hist.get('snapshots', [])):
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


def update_index_html(index_path, series, series_core=None, core_locations=None):
    with open(index_path, encoding='utf-8') as f:
        html = f.read()

    payload = json.dumps({
        'sessions': SESSIONS,
        'series': series,
        'series_core': series_core or {},
        'core_locations': core_locations or [],
    }, separators=(',', ':'))
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
    core_slugs = find_core_slugs(repo)
    series_core = build_series(repo, only_slugs=set(core_slugs))
    ok, msg = update_index_html(index_path, series, series_core, core_slugs)
    for name, pts in series.items():
        last = pts[-1]
        core_pts = series_core.get(name) or []
        core_last = core_pts[-1] if core_pts else None
        print('  %-14s %d points, latest %s: %d enrolled / %d capacity (%d locs)'
              % (name, len(pts), last['date'], last['enrolled'], last['capacity'], last['locations']))
        if core_last:
            print('    core cohort  %d points, latest %s: %d enrolled / %d capacity (%d locs)'
                  % (len(core_pts), core_last['date'], core_last['enrolled'], core_last['capacity'], core_last['locations']))
    print('  core cohort (%d locations): %s' % (len(core_slugs), ', '.join(sorted(core_slugs))))
    print(('OK  ' if ok else 'ERR ') + msg)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
