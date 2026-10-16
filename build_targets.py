#!/usr/bin/env python3
"""
Pre-enrollment targets for the session currently being pulled.

Same rule as the hand-built Fall 2026 targets (Jul 29): a location's goal is
80-85% of the seats it has posted, checked 5 weeks after the session starts
(Fall: Aug 31 start -> Oct 5 check-in). While the session is still in
pre-enrollment the seat count moves as FOSS posts or pulls classes, so the
target is recomputed from every pull; once a pull lands on or after the session
start it is locked and never touched again. Targets for other sessions (Fall's
hand-built ones included) are left alone.

Called from update_all_from_api.py after each pull. Standalone:

    python3 build_targets.py [slug ...]      # default: every FOSS location
"""
import json
import os
import sys
from datetime import date, timedelta

from append_history import SESSIONS, write_history

TARGET_LOW_PCT, TARGET_HIGH_PCT = 0.80, 0.85
CHECKIN_DAYS = 35

REPO = os.path.dirname(os.path.abspath(__file__))


def build_target(session, snap):
    meta = next((s for s in SESSIONS if s['name'] == session), None)
    if not meta:
        return None
    seats = snap['totals']['capacity']
    low, high = round(seats * TARGET_LOW_PCT), round(seats * TARGET_HIGH_PCT)
    return {
        'session': session,
        'totalSeats': seats,
        'classesOffered': len(snap.get('slots') or {}),
        'targetLowPct': TARGET_LOW_PCT,
        'targetHighPct': TARGET_HIGH_PCT,
        'targetLow': low,
        'targetHigh': high,
        'targetMid': round((low + high) / 2),
        'targetDate': (date.fromisoformat(meta['start']) + timedelta(days=CHECKIN_DAYS)).isoformat(),
        'asOf': snap['date'],
    }


def update_targets(slug, history_dir=None):
    """Returns a short status string."""
    history_dir = history_dir or os.path.join(REPO, 'history')
    path = os.path.join(history_dir, f'{slug}.json')
    if not os.path.exists(path):
        return 'no history'
    with open(path, encoding='utf-8') as f:
        hist = json.load(f)
    snaps = sorted((s for s in hist.get('snapshots', []) if (s.get('totals') or {}).get('capacity')),
                   key=lambda s: s['date'])
    if not snaps:
        return 'no pulls with seats'
    latest = snaps[-1]
    session = latest['session']
    meta = next((s for s in SESSIONS if s['name'] == session), None)
    if not meta:
        return f'{session}: not on the calendar'
    targets = hist.setdefault('targets', {})
    if latest['date'] >= meta['start'] and session in targets:
        return f'{session}: locked ({targets[session]["totalSeats"]} seats as of {targets[session]["asOf"]})'
    tgt = build_target(session, latest)
    if targets.get(session) == tgt:
        return f'{session}: unchanged'
    targets[session] = tgt
    write_history(history_dir, slug, hist)
    return (f'{session}: {tgt["targetLow"]}-{tgt["targetHigh"]} of {tgt["totalSeats"]} seats '
            f'({tgt["classesOffered"]} classes) by {tgt["targetDate"]}')


def main():
    from update_all_from_api import SLUGS
    slugs = sys.argv[1:] or list(SLUGS)
    for slug in slugs:
        print(f'  {slug}: {update_targets(slug)}')


if __name__ == '__main__':
    main()
