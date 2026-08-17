#!/usr/bin/env python3
"""
Rebuild index.html's location cards + the "Enrollment Overview" chart store
(<script id="fossEnrollData">) from data that is always on disk: each
`<slug>.html`'s live RAW_DATA/CAMP_RAW_DATA, and `history/<slug>.json` for
sessions that have closed.

Why this exists
---------------
`update_index.py` patches a card with a handful of regexes and, crucially,
bails out with `if block == orig: return False` BEFORE it writes the chart
store. The Fall panels added by hand on 2026-07-29 use different wording
("... seats posted", "Target: N-M by Oct 5", "2.4% utilized <span>... ramp")
than those regexes expect, so for every location added after the cohort the
whole card update no-op'd -- and took the store write down with it. Result on
2026-08-17: Ballwin's own page showed 714 while the homepage chart still had
520, its 2026-08-05 value; 17 of 35 locations were stale the same way. The
cohort locations had the mirror-image bug -- their *Summer* panel matched the
regexes first, so Fall numbers were being written into the Summer tab.

Rebuilding from data instead of patching text means the homepage cannot drift
from the dashboards again, and a rollover re-labels itself. Cards are ordered
live-session-first so update_index.py's count=1 regexes still land on the live
panel between rebuilds.

Usage:
    python3 rebuild_index.py [index.html]
"""
import json
import os
import re
import sys

from update_index import (DAY_ABBR, PILL_SEASONS, _fmt_range,
                          date_from_location_html, get_category, util_class)

REPO = os.path.dirname(os.path.abspath(__file__))
TARGET_FILL = 0.80
# Mirrors retrofit_session_freeze.py / build_agg_chart.py: a pull returning less
# than this share of the prior pull's seats was truncated by FOSS closing the
# session, not a real capacity cut.
TRUNC_RATIO = 0.95


def _decode_const(html, name):
    m = re.search(r'const %s = ' % name, html)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[m.end():])[0]
    except ValueError:
        return None


def load_dashboard(slug):
    path = os.path.join(REPO, f'{slug}.html')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        html = f.read()
    return {
        'raw': _decode_const(html, 'RAW_DATA') or [],
        'camp': _decode_const(html, 'CAMP_RAW_DATA') or [],
        'date': date_from_location_html(html),
        'name': (re.search(r'<h1>[^<]*School ([^<]*?) - ', html) or [None, slug])[1],
    }


def load_history(slug):
    path = os.path.join(REPO, 'history', f'{slug}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except ValueError:
        return None


def usable_snaps(hist):
    snaps = [s for s in (hist or {}).get('snapshots', [])
             if (s.get('totals') or {}).get('capacity', 0) > 0]
    snaps.sort(key=lambda s: s['date'])
    return snaps


def final_snapshot(snaps, session):
    """Newest COMPLETE pull of a session -- see TRUNC_RATIO."""
    lst = [s for s in snaps if s.get('session') == session]
    if not lst:
        return None
    for i in range(len(lst) - 1, 0, -1):
        if lst[i]['totals']['capacity'] >= lst[i - 1]['totals']['capacity'] * TRUNC_RATIO:
            return lst[i]
    return lst[0]


def live_totals(dash):
    e = s = 0
    for r in dash['raw']:
        try:
            e += int(r['Enrolled']); s += int(r['Total Capacity'])
        except (ValueError, KeyError, TypeError):
            continue
    ce = cs = 0
    for r in dash['camp']:
        try:
            ce += int(r['Enrolled']); cs += int(r['Total Capacity'])
        except (ValueError, KeyError, TypeError):
            continue
    return e, s, ce, cs


def live_pivot(dash):
    """Same shape update_index.build_pivot produces, straight off the dashboard."""
    agg = {}
    def add(rows, view, day_field):
        for r in rows:
            try:
                e = int(r['Enrolled']); s = int(r['Total Capacity'])
            except (ValueError, KeyError, TypeError):
                continue
            lvl = (r.get('Class Level') or '').strip()
            if view == 'weekly':
                d = (r.get(day_field) or '').strip()
                days = [DAY_ABBR.get(d, d[:3])] if d else []
            else:
                raw = (r.get(day_field) or '').strip()
                days = [x.strip()[:3] for x in re.split(r'[/,]', raw) if x.strip()] if raw else []
            key = (view, tuple(days), lvl)
            a = agg.get(key)
            if a is None:
                a = {'v': view, 'd': list(days), 'lvl': lvl, 'cat': get_category(lvl), 'e': 0, 's': 0}
                agg[key] = a
            a['e'] += e
            a['s'] += s
    add(dash['raw'], 'weekly', 'Day')
    add(dash['camp'], 'camp', 'Days')
    return list(agg.values())


def frozen_pivot(snap):
    """Rebuild a weekly pivot from a history snapshot's Day|HH:MM|Level slots."""
    agg = {}
    for key, (e, s) in (snap.get('slots') or {}).items():
        parts = key.split('|')
        if len(parts) != 3:
            continue
        day, _, lvl = parts
        k = ('weekly', (DAY_ABBR.get(day, day[:3]),), lvl)
        a = agg.get(k)
        if a is None:
            a = {'v': 'weekly', 'd': [DAY_ABBR.get(day, day[:3])], 'lvl': lvl,
                 'cat': get_category(lvl), 'e': 0, 's': 0}
            agg[k] = a
        a['e'] += e
        a['s'] += s
    return list(agg.values())


def _short_date(iso):
    try:
        from datetime import datetime
        return datetime.strptime(iso, '%Y-%m-%d').strftime('%b %-d')
    except (ValueError, TypeError):
        return None


def _long_date(iso):
    try:
        from datetime import datetime
        return datetime.strptime(iso, '%Y-%m-%d').strftime('%b %-d, %Y')
    except (ValueError, TypeError):
        return iso


def season_key(session):
    first = session.split()[0].lower() if session else ''
    return first if first in PILL_SEASONS else 'other'


def _pill(session, sessions_meta):
    meta = next((s for s in sessions_meta if s['name'] == session), None)
    dates = _fmt_range(meta['start'], meta['end']) if meta else ''
    dates_html = ' <span class="pill-dates">· %s</span>' % dates if dates else ''
    return ('<span class="session-pill sess-%s"><strong>%s</strong>%s</span>'
            % (season_key(session), session, dates_html))


def live_panel(session, sessions_meta, date, enrolled, seats, camp_e, camp_s, target):
    projected = round(TARGET_FILL * seats) if seats else 0
    pct = round(enrolled / projected * 100) if projected else 0
    util = round(enrolled / seats * 100, 1) if seats else 0.0
    rows = [
        '            <span class="meta-num"><strong>%d</strong> enrolled '
        '<span class="meta-soft">/ %s seats</span></span>' % (enrolled, f'{seats:,}'),
        '            <span class="meta-camp">Clinics: <strong>%d</strong> enrolled '
        '<span class="meta-soft">/ %s seats</span></span>' % (camp_e, f'{camp_s:,}'),
        '            <span class="meta-proj">Projected: <strong>%s</strong> (%d%% of target)</span>'
        % (f'{projected:,}', pct),
    ]
    lo, hi = (target or {}).get('targetLow'), (target or {}).get('targetHigh')
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        by = _short_date(target.get('targetDate'))
        rows.append('            <span class="meta-target">Target: <strong>%s&ndash;%s</strong>%s '
                    '<span class="meta-soft">(%d&ndash;%d%% of seats offered)</span></span>'
                    % (f'{round(lo):,}', f'{round(hi):,}', (' by ' + by) if by else '',
                       round((target.get('targetLowPct') or 0) * 100),
                       round((target.get('targetHighPct') or 0) * 100)))
    rows.append('            <span class="util util-%s">%.1f%% utilized</span>'
                % (util_class(util), util))
    return ('        <div class="card-panel" data-panel="%s">\n'
            '          <div class="card-meta">\n'
            '            %s\n'
            '            <span class="meta-date">Updated %s</span>\n'
            '          </div>\n'
            '          <div class="card-stats">\n%s\n          </div>\n'
            '        </div>' % (season_key(session), _pill(session, sessions_meta),
                                date or 'unknown', '\n'.join(rows)))


def frozen_panel(session, sessions_meta, snap, camp_e):
    t = snap['totals']
    util = t.get('utilization') or (round(t['enrolled'] / t['capacity'] * 100, 1) if t['capacity'] else 0.0)
    date = snap['date']
    rows = ['            <span class="meta-num"><strong>%d</strong> enrolled '
            '<span class="meta-soft">/ %s seats</span></span>' % (t['enrolled'], f"{t['capacity']:,}")]
    if camp_e:
        rows.append('            <span class="meta-camp">Clinics: <strong>%d</strong> enrolled</span>' % camp_e)
    rows.append('            <span class="util util-%s">%.1f%% utilized '
                '<span class="meta-soft">&mdash; final, session closed</span></span>'
                % (util_class(util), util))
    return ('        <div class="card-panel" data-panel="%s" hidden>\n'
            '          <div class="card-meta">\n'
            '            %s\n'
            '            <span class="meta-date">Final &middot; last complete pull %s</span>\n'
            '          </div>\n'
            '          <div class="card-stats">\n%s\n          </div>\n'
            '        </div>' % (season_key(session), _pill(session, sessions_meta),
                                _long_date(date), '\n'.join(rows)))


def absent_panel(session, first_date):
    note = ('Added to inventory %s &mdash; no %s data' % (_long_date(first_date), session)
            if first_date else 'Not tracked this session')
    return ('        <div class="card-panel" data-panel="%s" hidden>\n'
            '          <div class="card-meta">\n'
            '            <span class="meta-date">Not tracked this session</span>\n'
            '          </div>\n'
            '          <div class="card-stats">\n'
            '            <span class="util util-na">%s</span>\n'
            '          </div>\n'
            '        </div>' % (season_key(session), note))


def rebuild(index_path):
    from update_all_from_api import SLUGS
    with open(index_path, encoding='utf-8') as f:
        html = f.read()

    # Pass 1: work out each location's live session, frozen finals and totals.
    locs, all_sessions = {}, []
    for slug in SLUGS:
        dash, hist = load_dashboard(slug), load_history(slug)
        if not dash or not hist:
            continue
        snaps = usable_snaps(hist)
        recorded = sorted(hist.get('snapshots', []), key=lambda s: s['date'])
        live = (snaps[-1]['session'] if snaps
                else (recorded[-1]['session'] if recorded else None))
        if not live:
            continue
        names = []
        for s in snaps:
            if s['session'] not in names:
                names.append(s['session'])
        locs[slug] = {
            'dash': dash, 'hist': hist, 'snaps': snaps, 'live': live,
            'sessions': names,
            'first_date': recorded[0]['date'] if recorded else None,
        }
        for n in names + [live]:
            if n not in all_sessions:
                all_sessions.append(n)

    # Only sessions the chart store should offer as tabs: the live one plus any
    # session with a real frozen final somewhere. Newest first.
    order = [s['name'] for s in (locs and next(iter(locs.values()))['hist'].get('sessions') or [])]
    all_sessions.sort(key=lambda n: order.index(n) if n in order else -1, reverse=True)

    store_m = re.search(r'<script id="fossEnrollData" type="application/json">([\s\S]*?)</script>', html)
    store = json.loads(store_m.group(1)) if store_m else {}

    # Stay inside the set of sessions the homepage already offers, plus whatever
    # is being pulled now. History also holds partial Spring 2026 pulls that were
    # never surfaced here; back-filling them would silently add a tab and a chart
    # option nobody asked for.
    known = {s for v in store.values() for s in v} | {L['live'] for L in locs.values()}
    all_sessions = [s for s in all_sessions if s in known]

    changed, notes = [], []
    for slug, L in locs.items():
        dash, hist = L['dash'], L['hist']
        sessions_meta = hist.get('sessions') or []
        targets = hist.get('targets') or {}
        enrolled, seats, camp_e, camp_s = live_totals(dash)
        prev = (store.get(slug) or {})
        if seats <= 0:
            # Catalog has never come back populated for this location; leave its
            # card and its absence from the chart alone rather than drawing a
            # zero bar that reads as "no enrollment".
            notes.append(f'{slug}: no seats in latest pull -- card left untouched')
            continue

        # --- store: live entry always regenerated from the dashboard itself ---
        loc_name = (prev.get(L['live']) or {}).get('name') or dash['name']
        entry = {'name': loc_name, 'weekly': enrolled, 'camp': camp_e, 'pivot': live_pivot(dash)}
        before = (prev.get(L['live']) or {}).get('weekly')
        if before != enrolled:
            notes.append(f"{slug}: chart store {L['live']} {before} -> {enrolled}")
        store.setdefault(slug, {})[L['live']] = entry

        # --- store: frozen entries pinned to each closed session's final pull ---
        for session in L['sessions']:
            if session == L['live'] or session not in all_sessions:
                continue
            snap = final_snapshot(L['snaps'], session)
            if not snap:
                continue
            old = prev.get(session) or {}
            if old.get('weekly') != snap['totals']['enrolled']:
                notes.append(f"{slug}: chart store {session} {old.get('weekly')} -> "
                             f"{snap['totals']['enrolled']} (frozen at {snap['date']})")
                store[slug][session] = {'name': loc_name, 'weekly': snap['totals']['enrolled'],
                                        'camp': old.get('camp', 0), 'pivot': frozen_pivot(snap)}

        # --- card: live panel first, then each closed session, then absentees ---
        tabs, panels = [], []
        for session in all_sessions:
            key, label = season_key(session), session.split()[0]
            active = ' is-active' if session == L['live'] else ''
            if session == L['live']:
                panels.append(live_panel(session, sessions_meta, dash['date'], enrolled, seats,
                                         camp_e, camp_s, targets.get(session)))
            elif session in L['sessions']:
                snap = final_snapshot(L['snaps'], session)
                if not snap:
                    continue
                panels.append(frozen_panel(session, sessions_meta, snap,
                                           (prev.get(session) or {}).get('camp', 0)))
            else:
                panels.append(absent_panel(session, L['first_date']))
            tabs.append('          <span class="card-tab%s" data-tab="%s" '
                        'onclick="return fossTab(event,this)">%s</span>' % (active, key, label))

        block = ('        <div class="card-tabs">\n%s\n        </div>\n%s'
                 % ('\n'.join(tabs), '\n'.join(panels)))

        i = html.find('href="%s.html"' % slug)
        if i < 0:
            notes.append(f'{slug}: no card in index.html (skipped)')
            continue
        j = html.find('</a>', i)
        card = html[i:j]
        k = card.find('<div class="card-tabs">')
        if k < 0:
            notes.append(f'{slug}: card has no tabs block (skipped)')
            continue
        new_card = card[:k].rstrip('\n ') + '\n' + block + '\n      '
        if new_card != card:
            changed.append(slug)
        html = html[:i] + new_card + html[j:]

    html = update_store_blob(html, store)
    html = sync_chart_session_consts(html, store, all_sessions)

    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return changed, notes


def update_store_blob(html, store):
    m = re.search(r'(<script id="fossEnrollData" type="application/json">)([\s\S]*?)(</script>)', html)
    if not m:
        return html
    return html[:m.start(2)] + json.dumps(store, ensure_ascii=False) + html[m.end(2):]


def sync_chart_session_consts(html, store, all_sessions):
    """Derive the chart's session list + "aggregate only" set from the store
    rather than the hardcoded literals, which went stale at the rollover (Fall
    was still flagged as pre-season-only long after real per-class Fall data
    started arriving)."""
    order = list(reversed(all_sessions))  # calendar order for the selector
    html = re.sub(r'var SESSION_ORDER = \[[^\]]*\];',
                  'var SESSION_ORDER = %s;' % json.dumps(order),
                  html, count=1)
    agg_only = {}
    for session in all_sessions:
        entries = [v[session] for v in store.values() if session in v]
        if entries and not any(e.get('pivot') for e in entries):
            agg_only[session] = True
    return re.sub(r'var AGGREGATE_ONLY_SESSIONS = \{[^}]*\};',
                  'var AGGREGATE_ONLY_SESSIONS = %s;' % json.dumps(agg_only),
                  html, count=1)


def main():
    index_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, 'index.html')
    changed, notes = rebuild(index_path)
    for n in notes:
        print('  ' + n)
    print('%d cards rebuilt in %s' % (len(changed), os.path.basename(index_path)))


if __name__ == '__main__':
    main()
