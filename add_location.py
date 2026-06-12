#!/usr/bin/env python3
"""
Scaffold a NEW FOSS location into the FOFNIntel dashboards (one-off Mode C).

Registers a facility everywhere it needs to exist, then the normal
extract -> update_all_from_api flow will populate it like any tracked location.

What it does:
  1. foss_api_extract.js   : add  <facilityId>:'<slug>'  to FACILITIES
  2. update_all_from_api.py : add  '<slug>': '<Location, ST>'  to SLUGS
  3. <slug>.html            : create from a template dashboard (default northglenn.html)
  4. index.html             : insert a homepage card (stats are placeholders that
                              update_index.py fills on the first run)

Usage:
  python3 add_location.py <facilityId> <slug> "<Location, ST>" [--template <slug>] [--repo <dir>]

Example:
  python3 add_location.py 21 ballwin "Ballwin, MO"

After running: extract that facility (window.fossApiExtract([<facilityId>])),
save its CSV to <csv_dir>/foss_api_csv_<slug>.csv, then:
  python3 update_all_from_api.py <csv_dir> --only <slug>
"""
import json
import os
import re
import sys


def _insert_facilities(repo, fid, slug):
    p = os.path.join(repo, 'foss_api_extract.js')
    js = open(p, encoding='utf-8').read()
    entry = "%d:'%s'" % (fid, slug)
    if re.search(r'\b%d:' % fid, js.split('LEVEL_NAMES')[0]):
        return 'FACILITIES: id %d already present (skipped)' % fid
    new = re.sub(r'(var FACILITIES = \{.*?)(\n  \};)',
                 lambda m: m.group(1) + ', ' + entry + m.group(2), js, count=1, flags=re.S)
    if new == js:
        raise RuntimeError('could not locate FACILITIES block')
    open(p, 'w', encoding='utf-8').write(new)
    return 'FACILITIES += %s' % entry


def _insert_slugs(repo, slug, label):
    p = os.path.join(repo, 'update_all_from_api.py')
    py = open(p, encoding='utf-8').read()
    if re.search(r"'%s'\s*:" % re.escape(slug), py.split('def main')[0]):
        return 'SLUGS: %s already present (skipped)' % slug
    new = re.sub(r'(SLUGS = \{.*?)(\n\})',
                 lambda m: m.group(1) + ("\n    '%s': %r," % (slug, label)) + m.group(2),
                 py, count=1, flags=re.S)
    if new == py:
        raise RuntimeError('could not locate SLUGS block')
    open(p, 'w', encoding='utf-8').write(new)
    return "SLUGS += '%s': %r" % (slug, label)


def _create_html(repo, slug, label, template):
    dst = os.path.join(repo, '%s.html' % slug)
    if os.path.exists(dst):
        return '%s.html already exists (skipped)' % slug
    src = os.path.join(repo, '%s.html' % template)
    if not os.path.exists(src):
        raise RuntimeError('template %s.html not found' % template)
    html = open(src, encoding='utf-8').read()
    # The template carries the "Enrollment Over Time" panel, which loads
    # history/<template>.js — repoint it at this location's own history file
    # (otherwise the new page would display the template location's history).
    html = html.replace('history/%s.js' % template, 'history/%s.js' % slug)
    open(dst, 'w', encoding='utf-8').write(html)
    return '%s.html created from %s.html (run update_dashboard to populate)' % (slug, template)


def _create_history_stub(repo, slug, label):
    """Empty history so the trend panel renders before the first pull
    (update_dashboard.py's append overwrites this with real snapshots)."""
    hist_dir = os.path.join(repo, 'history')
    jpath = os.path.join(hist_dir, '%s.json' % slug)
    if os.path.exists(jpath):
        return 'history/%s.json already present (skipped)' % slug
    os.makedirs(hist_dir, exist_ok=True)
    try:
        sys.path.insert(0, repo)
        from append_history import SESSIONS
    except Exception:
        SESSIONS = []
    hist = {"location": label, "slug": slug, "sessions": SESSIONS, "snapshots": []}
    with open(jpath, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(hist_dir, '%s.js' % slug), 'w', encoding='utf-8') as f:
        f.write('const ENROLLMENT_HISTORY = ')
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    return 'history stub created (history/%s.json + .js)' % slug


def _register_history_tools(repo, slug, label):
    """Keep backfill_history.py / retrofit_trend_panel.py slug lists in sync."""
    msgs = []
    p = os.path.join(repo, 'backfill_history.py')
    if os.path.exists(p):
        py = open(p, encoding='utf-8').read()
        if not re.search(r"'%s'\s*:" % re.escape(slug), py):
            new = re.sub(r'(SLUGS = \{.*?)(\n\})',
                         lambda m: m.group(1) + ("\n    '%s': %r," % (slug, label)) + m.group(2),
                         py, count=1, flags=re.S)
            if new != py:
                open(p, 'w', encoding='utf-8').write(new)
                msgs.append('backfill_history.py')
    p = os.path.join(repo, 'retrofit_trend_panel.py')
    if os.path.exists(p):
        py = open(p, encoding='utf-8').read()
        if 'SLUGS = [' in py and ("'%s'" % slug) not in py[py.index('SLUGS = ['):]:
            new = re.sub(r'(SLUGS = \[.*?)\]',
                         lambda m: m.group(1).rstrip() + (",\n         '%s']" % slug),
                         py, count=1, flags=re.S)
            if new != py:
                open(p, 'w', encoding='utf-8').write(new)
                msgs.append('retrofit_trend_panel.py')
    return ('slug registered in ' + ', '.join(msgs)) if msgs else 'history tools already in sync'


def _insert_card(repo, slug, label):
    p = os.path.join(repo, 'index.html')
    html = open(p, encoding='utf-8').read()
    if 'href="%s.html"' % slug in html:
        return 'index card for %s already present (skipped)' % slug
    # reuse the current season pill from the first existing FOSS card
    pill_m = re.search(r'<span class="session-pill.*?</span></span>', html, re.S)
    pill = pill_m.group(0) if pill_m else \
        '<span class="session-pill sess-summer"><strong>Season</strong></span>'
    card = (
        '      <a class="card foss-card" href="%s.html">\n'
        '        <h2>%s</h2>\n'
        '        <div class="card-meta">\n'
        '          %s\n'
        '          <span class="meta-date">Updated —</span>\n'
        '        </div>\n'
        '        <div class="card-stats">\n'
        '          <span class="meta-num"><strong>0</strong> enrolled <span class="meta-soft">/ 0 seats</span></span>\n'
        '          <span class="meta-proj">Projected: <strong>0</strong> (0%% of target)</span>\n'
        '          <span class="util util-low">0.0%% utilized</span>\n'
        '        </div>\n'
        '      </a>\n'
    ) % (slug, label, pill)
    # insert right after the last FOSS card's closing </a> (keeps the
    # container </div> and its indentation untouched)
    anchor = html.find('<h3 class="section intel"')
    if anchor < 0:
        anchor = len(html)
    last_a = html.rfind('</a>', 0, anchor)
    if last_a < 0:
        raise RuntimeError('could not find a FOSS card to anchor after')
    nl = html.find('\n', last_a)
    insert_pos = (nl + 1) if nl >= 0 else last_a + len('</a>')
    html = html[:insert_pos] + card + html[insert_pos:]
    open(p, 'w', encoding='utf-8').write(html)
    return 'index card inserted for %s' % slug


def main():
    args = [a for a in sys.argv[1:]]
    repo = os.path.dirname(os.path.abspath(__file__))
    template = 'northglenn'
    if '--repo' in args:
        i = args.index('--repo'); repo = args[i + 1]; del args[i:i + 2]
    if '--template' in args:
        i = args.index('--template'); template = args[i + 1]; del args[i:i + 2]
    if len(args) < 3:
        print(__doc__); sys.exit(1)
    fid = int(args[0]); slug = args[1].strip(); label = args[2].strip()
    if not re.fullmatch(r'[a-z0-9_]+', slug):
        print('slug must be lowercase letters/numbers/underscore'); sys.exit(1)

    for step in (
        lambda: _insert_facilities(repo, fid, slug),
        lambda: _insert_slugs(repo, slug, label),
        lambda: _create_html(repo, slug, label, template),
        lambda: _create_history_stub(repo, slug, label),
        lambda: _register_history_tools(repo, slug, label),
        lambda: _insert_card(repo, slug, label),
    ):
        print('  ' + step())

    print('\nDone. Next:')
    print('  1. Extract it:  window.fossApiExtract([%d])  (in the FOSS tab)' % fid)
    print('  2. Save CSV to <csv_dir>/foss_api_csv_%s.csv' % slug)
    print('  3. python3 update_all_from_api.py <csv_dir> --only %s' % slug)


if __name__ == '__main__':
    main()
