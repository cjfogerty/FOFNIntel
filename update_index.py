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
import re
import sys

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
