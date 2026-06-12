#!/usr/bin/env python3
"""
Homepage card updater for FOFNIntel (index.html).

Recomputes a single location's summary card from its weekly CSV and rewrites the
matching <a href="<slug>.html"> card in index.html, in place. Called by
update_all_from_api.py after each location dashboard is regenerated, so the
homepage cards stay in sync with the per-location pages on every run.

Card fields (methodology mirrors update_dashboard.py):
  enrolled / seats : summed over all weekly CSV rows (group + private + preview)
  utilization      : round(enrolled / seats * 100, 1)
  projected        : round(0.80 * seats)            # 80%-of-capacity fill target
  % of target      : round(enrolled / projected * 100)
  util color class : <50 -> low, 50-<70 -> mid, >=70 -> high
  date             : read from the location page's "Data extracted:" line so the
                     card's "Updated <date>" always matches the dashboard it links to.
"""
import csv
import json
import os
import re
import sys
from datetime import datetime

TARGET_FILL = 0.80


def compute_stats(csv_path):
    enrolled = seats = 0
    with open(csv_path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                enrolled += int(r['Enrolled'])
                seats += int(r['Total Capacity'])
            except (ValueError, KeyError, TypeError):
                continue
    if seats <= 0:
        return None
    projected = round(TARGET_FILL * seats)
    pct = round(enrolled / projected * 100) if projected > 0 else 0
    return {
        'enrolled': enrolled,
        'seats': seats,
        'util': round(enrolled / seats * 100, 1),
        'projected': projected,
        'pct': pct,
    }


def util_class(util):
    if util < 50:
        return 'low'
    if util < 70:
        return 'mid'
    return 'high'


def date_from_location_html(html):
    m = re.search(r'Data extracted:\s*([A-Z][a-z]+ \d{1,2}, \d{4})', html)
    return m.group(1) if m else None



PILL_SEASONS = {'spring', 'summer', 'fall', 'winter'}


def _fmt_range(start, end):
    try:
        a = datetime.strptime(start, '%Y-%m-%d').strftime('%b %-d')
        b = datetime.strptime(end, '%Y-%m-%d').strftime('%b %-d')
        return '%s \u2013 %s' % (a, b)  # en dash
    except (ValueError, TypeError):
        return None


def season_pill(index_path, slug):
    """Return (label, css_suffix, date_range) for the session this location is
    currently showing. Source of truth is the location's latest history snapshot
    (the session the data was actually pulled for, e.g. "Summer 2026"), which
    append_history.infer_session() sets from each session's enrollment-open
    (catalog_from) date -- so it flips to the new session as soon as the catalog
    opens, even before the calendar rolls over. Falls back to the live calendar.
    """
    repo = os.path.dirname(os.path.abspath(index_path))
    try:
        from append_history import SESSIONS, infer_session
    except Exception:
        SESSIONS, infer_session = [], None

    label = None
    hist_path = os.path.join(repo, 'history', '%s.json' % slug)
    try:
        with open(hist_path, encoding='utf-8') as f:
            snaps = json.load(f).get('snapshots', [])
        if snaps:
            label = snaps[-1].get('session')
    except (OSError, ValueError):
        pass
    if not label and infer_session:
        label = infer_session()
    if not label:
        return None

    suffix = 'other'
    first = label.split()[0].lower() if label.split() else ''
    if first in PILL_SEASONS:
        suffix = first
    date_range = None
    for sess in SESSIONS:
        if sess.get('name') == label:
            date_range = _fmt_range(sess.get('start'), sess.get('end'))
            break
    return label, suffix, date_range


def update_index_card(index_path, slug, csv_path, location_html_path):
    stats = compute_stats(csv_path)
    if not stats:
        return False, 'no seats computed from %s' % csv_path

    date = None
    try:
        with open(location_html_path, encoding='utf-8') as f:
            date = date_from_location_html(f.read())
    except OSError:
        pass

    with open(index_path, encoding='utf-8') as f:
        html = f.read()

    i = html.find('href="%s.html"' % slug)
    if i < 0:
        return False, 'no card for %s in index.html (skipped)' % slug
    j = html.find('</a>', i)
    block = orig = html[i:j]

    seats_str = '{:,}'.format(stats['seats'])
    proj_str = '{:,}'.format(stats['projected'])

    if date:
        block = re.sub(r'(<span class="meta-date">Updated )[^<]*(</span>)',
                       lambda m: m.group(1) + date + m.group(2), block, count=1)
    block = re.sub(
        r'(<span class="meta-num"><strong>)[\d,]+(</strong> enrolled <span class="meta-soft">/ )[\d,]+( seats</span></span>)',
        lambda m: '%s%d%s%s%s' % (m.group(1), stats['enrolled'], m.group(2), seats_str, m.group(3)),
        block, count=1)
    block = re.sub(
        r'(<span class="meta-proj">Projected: <strong>)[\d,]+(</strong> \()\d+(% of target\)</span>)',
        lambda m: '%s%s%s%d%s' % (m.group(1), proj_str, m.group(2), stats['pct'], m.group(3)),
        block, count=1)
    block = re.sub(
        r'<span class="util util-(?:low|mid|high)">[\d.]+% utilized</span>',
        '<span class="util util-%s">%.1f%% utilized</span>' % (util_class(stats['util']), stats['util']),
        block, count=1)

    sp = season_pill(index_path, slug)
    if sp:
        label, suffix, date_range = sp
        dates_html = (' <span class="pill-dates">\u00b7 %s</span>' % date_range) if date_range else ''
        new_pill = '<span class="session-pill sess-%s"><strong>%s</strong>%s</span>' % (suffix, label, dates_html)
        block = re.sub(
            r'<span class="session-pill sess-[a-z]+"><strong>[^<]*</strong>'
            r'(?: <span class="pill-dates">[^<]*</span>)?</span>',
            lambda m: new_pill, block, count=1)

    if block == orig:
        return False, 'card found but no fields matched for %s' % slug
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html[:i] + block + html[j:])
    return True, '%s -> %d/%s seats, %.1f%% util, proj %s (%d%% of target)%s' % (
        slug, stats['enrolled'], seats_str, stats['util'], proj_str, stats['pct'],
        '' if date else '  [date unchanged: none found]')


if __name__ == '__main__':
    if len(sys.argv) < 5:
        print('usage: update_index.py <index.html> <slug> <weekly_csv> <location.html>')
        sys.exit(1)
    ok, msg = update_index_card(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    print(('OK  ' if ok else 'ERR ') + msg)
    sys.exit(0 if ok else 1)
