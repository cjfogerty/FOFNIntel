#!/usr/bin/env python3
"""
Retrofit session-accurate labelling + a frozen "final session" view into the
production <slug>.html dashboards, in place. Idempotent -- safe to re-run on
every pull (update_all_from_api.py calls it).

Why this exists
---------------
The dashboard body (KPI cards, charts, tables) is rebuilt from whatever the
extractor pulled that morning. When FOSS rolls the catalog to the next season
the extractor follows it, so the body silently becomes the NEW session's data
while the page still carried a hardcoded "Summer 2026 Session:" header and a
Session dropdown that defaulted to "summer". That is how a Fall KPI (370) ended
up sitting above a Summer trend line ending at 707.

You cannot fix that by re-deriving the old session from a later pull: once FOSS
closes a session to new enrollment its classes stop coming back from the API
(Westminster lost 23 of its 24 Sunday Summer classes between the Jul 16 and
Jul 19 pulls), so any later "Summer" total is truncated, not real.

So: the live body is always labelled with the session actually pulled, and each
past session is FROZEN at its last complete pull, read from ENROLLMENT_HISTORY.
All of that is decided in the browser from history/<slug>.js, so the next
quarter rollover relabels itself with no code change.

Usage:
    python3 retrofit_session_freeze.py --all
    python3 retrofit_session_freeze.py ofallon westminster
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
MARKER = 'frozen-session-freeze-v1'

FREEZE_CSS = """
        /* === frozen-session panel (frozen-session-freeze-v1) === */
        .frozen-kpis { display: flex; gap: 20px; margin: 4px 0 18px; }
        .frozen-note { font-size: 0.92rem; color: #4a5568; line-height: 1.6; }
        .frozen-warn { font-size: 0.85rem; color: #975a16; background: #fffaf0; border-left: 3px solid #ed8936; padding: 9px 13px; border-radius: 4px; margin-top: 13px; line-height: 1.5; }
        .frozen-foot { font-size: 0.75rem; color: #a0aec0; margin-top: 13px; line-height: 1.5; }
        .frozen-tables { display: flex; gap: 20px; flex-wrap: wrap; margin-top: 20px; }
        .frozen-table-wrap { flex: 1; min-width: 260px; }
        .frozen-table-title { font-size: 12px; font-weight: 600; color: #4a5568; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
        table.frozen-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        table.frozen-table th, table.frozen-table td { padding: 6px 10px; border-bottom: 1px solid #edf2f7; text-align: left; }
        table.frozen-table th { background: #f7fafc; font-weight: 600; color: #4a5568; }
        table.frozen-table td.num, table.frozen-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
        .modal-session { font-size: 11px; font-weight: 700; color: #718096; text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 6px; }
"""

FREEZE_PANEL = """        <div class="chart-card" id="frozenSessionPanel" style="display:none;">
            <div class="chart-title" id="frozenSessionTitle">Final Session Enrollment</div>
            <div class="frozen-kpis" id="frozenSessionKpis"></div>
            <div class="frozen-note" id="frozenSessionBody">Loading&hellip;</div>
            <div id="frozenSessionWarn"></div>
            <div class="frozen-tables" id="frozenSessionTables"></div>
            <div class="frozen-foot" id="frozenSessionFoot"></div>
        </div>
"""

SESSION_INFO_TPL = ('<div class="session-info"><strong id="sessionInfoName">Session:</strong> '
                    '<span id="sessionInfoDates"></span> &nbsp;&bull;&nbsp; '
                    '<span id="sessionInfoOpen"></span> &nbsp;&bull;&nbsp; {clause}</div>')

FREEZE_JS = """    <script>
    /* frozen-session-freeze-v1
       The dashboard body always shows the session the extractor actually pulled.
       Past sessions are frozen at their last COMPLETE pull, read from
       ENROLLMENT_HISTORY -- FOSS drops a session's classes from the API once it
       closes to new enrollment, so later pulls under-count it and must not be
       used to re-derive its totals. Everything below is driven by history, so a
       quarter rollover relabels itself. */
    (function () {
        var HIDE_SELECTORS = ['.filters .filter-group:not(#sessionFilterGroup):not(#locationSwitcherGroup)',
                              '.headline-modals', '.chart-grid', '.chart-card.full-width', '.table-card'];
        // A pull returning less than this share of the previous pull's seats is a
        // session-close truncation, not a real capacity cut.
        var TRUNC_RATIO = 0.95;
        var DAY_ORDER = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
        var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        var CAT_PREFIXES = [['Preview','Previews'],['Backfloat Baby','Backfloat Baby'],['Little','Littles'],
                            ['Middle','Middles'],['Big','Bigs'],['10+','10+'],['Adult','Adults'],
                            ['Private Lesson','Privates']];

        function hist() { return (typeof ENROLLMENT_HISTORY === 'undefined') ? null : ENROLLMENT_HISTORY; }
        function num(n) { return (n || 0).toLocaleString(); }
        function pct(e, c) { return c > 0 ? (e / c * 100).toFixed(1) : '0.0'; }
        function fmtShort(iso) { var p = iso.split('-'); return MONTHS[parseInt(p[1], 10) - 1] + ' ' + parseInt(p[2], 10); }
        function fmtLong(iso) { var p = iso.split('-'); return MONTHS[parseInt(p[1], 10) - 1] + ' ' + parseInt(p[2], 10) + ', ' + p[0]; }
        function usDate(iso) { var p = iso.split('-'); return parseInt(p[1], 10) + '/' + parseInt(p[2], 10) + '/' + p[0].slice(2); }
        function catOf(level) {
            for (var i = 0; i < CAT_PREFIXES.length; i++)
                if (level.indexOf(CAT_PREFIXES[i][0]) === 0) return CAT_PREFIXES[i][1];
            return 'Other';
        }
        function to12h(hhmm) {
            var p = hhmm.split(':'), h = parseInt(p[0], 10), ap = h >= 12 ? 'PM' : 'AM';
            h = h % 12; if (h === 0) h = 12;
            return h + ':' + p[1] + ' ' + ap;
        }

        // Failed pulls land in history as 0/0 -- never let one define a session.
        function usableSnaps() {
            var h = hist(); if (!h) return [];
            return (h.snapshots || []).filter(function (s) { return s.totals && s.totals.capacity > 0; })
                .slice().sort(function (a, b) { return a.date < b.date ? -1 : 1; });
        }
        function sessionMeta(name) {
            var h = hist(), found = null;
            if (h) (h.sessions || []).forEach(function (s) { if (s.name === name) found = s; });
            return found;
        }

        // Newest COMPLETE pull for a session: walk back past any pull whose seat
        // count collapsed against the pull before it.
        function finalFor(snaps, session) {
            var list = snaps.filter(function (s) { return s.session === session; });
            if (!list.length) return null;
            for (var i = list.length - 1; i > 0; i--) {
                if (list[i].totals.capacity >= list[i - 1].totals.capacity * TRUNC_RATIO)
                    return { snap: list[i], dropped: list.slice(i + 1) };
            }
            return { snap: list[0], dropped: list.slice(1) };
        }

        function aggregate(slots, keyOf) {
            var m = {}, order = [];
            for (var k in slots) {
                var i = k.indexOf('|'), j = k.indexOf('|', i + 1);
                var g = keyOf(k.slice(0, i), k.slice(i + 1, j), k.slice(j + 1));
                if (!m[g]) { m[g] = { name: g, lessons: 0, cap: 0, enr: 0 }; order.push(g); }
                m[g].lessons++; m[g].cap += slots[k][1]; m[g].enr += slots[k][0];
            }
            return order.map(function (g) { return m[g]; });
        }

        function tableHtml(title, label, rows) {
            var h = '<div class="frozen-table-wrap"><div class="frozen-table-title">' + title + '</div>';
            h += '<table class="frozen-table"><thead><tr><th>' + label + '</th><th class="num">Classes</th>' +
                 '<th class="num">Seats</th><th class="num">Enrolled</th><th class="num">Util</th></tr></thead><tbody>';
            rows.forEach(function (r) {
                h += '<tr><td>' + r.name + '</td><td class="num">' + r.lessons + '</td><td class="num">' +
                     num(r.cap) + '</td><td class="num">' + num(r.enr) + '</td><td class="num">' +
                     pct(r.enr, r.cap) + '%</td></tr>';
            });
            return h + '</tbody></table></div>';
        }

        function renderFrozen(session) {
            var snaps = usableSnaps();
            var res = finalFor(snaps, session);
            var title = document.getElementById('frozenSessionTitle');
            var kpis = document.getElementById('frozenSessionKpis');
            var body = document.getElementById('frozenSessionBody');
            var warn = document.getElementById('frozenSessionWarn');
            var tables = document.getElementById('frozenSessionTables');
            var foot = document.getElementById('frozenSessionFoot');
            if (!body) return;
            if (!res) {
                title.textContent = session;
                kpis.innerHTML = ''; tables.innerHTML = ''; warn.innerHTML = ''; foot.innerHTML = '';
                body.textContent = 'No data captured for ' + session + ' at this location.';
                return;
            }
            var s = res.snap, t = s.totals;
            title.innerHTML = session + ' &mdash; Final (frozen ' + fmtLong(s.date) + ')';
            kpis.innerHTML =
                '<div class="headline-modal"><div class="modal-session">' + session + ' &middot; final</div>' +
                '<div class="modal-value">' + num(t.enrolled) + '</div>' +
                '<div class="modal-label">Total Enrollments</div>' +
                '<div class="modal-subscript">of ' + num(t.capacity) + ' total capacity</div></div>' +
                '<div class="headline-modal"><div class="modal-session">' + session + ' &middot; final</div>' +
                '<div class="modal-value">' + pct(t.enrolled, t.capacity) + '%</div>' +
                '<div class="modal-label">Total Utilization</div></div>';

            var meta = sessionMeta(session);
            var run = meta ? (fmtShort(meta.start) + ' &ndash; ' + fmtLong(meta.end)) : '';
            body.innerHTML = '<strong>' + num(t.enrolled) + '</strong> enrolled of <strong>' + num(t.capacity) +
                '</strong> seats (<strong>' + pct(t.enrolled, t.capacity) + '%</strong>) across ' +
                Object.keys(s.slots).length + ' weekly classes' + (run ? ', session running ' + run : '') +
                '. Captured ' + fmtLong(s.date) + ' &mdash; the last complete pull of this session.';

            if (res.dropped.length) {
                var d = res.dropped[res.dropped.length - 1];
                var lostClasses = Object.keys(s.slots).length - Object.keys(d.slots).length;
                warn.innerHTML = '<div class="frozen-warn"><strong>Later pulls excluded.</strong> The ' +
                    fmtLong(d.date) + ' pull returned only ' + num(d.totals.capacity) + ' of ' + num(t.capacity) +
                    ' seats' + (lostClasses > 0 ? ' (' + lostClasses + ' classes missing)' : '') +
                    ' &mdash; FOSS had begun dropping ' + session + ' classes from the catalog as it closed to new ' +
                    'enrollment. Those classes ran; their enrollment did not disappear. ' + fmtLong(s.date) +
                    ' is used instead.</div>';
            } else {
                warn.innerHTML = '';
            }

            var byDay = aggregate(s.slots, function (day) { return day; });
            byDay.sort(function (a, b) { return DAY_ORDER.indexOf(a.name) - DAY_ORDER.indexOf(b.name); });
            var byCat = aggregate(s.slots, function (day, time, level) { return catOf(level); });
            byCat.sort(function (a, b) { return b.enr - a.enr; });
            tables.innerHTML = tableHtml('By day of week', 'Day', byDay) +
                               tableHtml('By curriculum category', 'Category', byCat);

            foot.innerHTML = 'Frozen on purpose: once FOSS closes a session to new enrollment its classes stop ' +
                'coming back from the API, so this total cannot be recomputed from a later pull &mdash; it would ' +
                'come back short. The live dashboard above always shows the session currently being pulled. The ' +
                'Enrollment Over Time chart below is already session-aware and has been switched to ' + session + '.';
        }

        function setSessionInfo(name, isFrozen, frozenDate) {
            var meta = sessionMeta(name);
            var el = document.getElementById('sessionInfoName');
            var d = document.getElementById('sessionInfoDates');
            var op = document.getElementById('sessionInfoOpen');
            if (el) el.textContent = name + ' Session:';
            if (d && meta) d.textContent = fmtShort(meta.start) + ' \\u2013 ' + fmtLong(meta.end);
            if (op) {
                if (isFrozen) op.textContent = 'Final \\u2014 frozen at the ' + fmtLong(frozenDate) + ' pull';
                else if (meta && meta.catalog_from) op.textContent = 'Enrollment opened ' + usDate(meta.catalog_from);
            }
        }

        function tagLiveKpis(session) {
            document.querySelectorAll('.headline-modals .headline-modal').forEach(function (el) {
                var tag = el.querySelector('.modal-session');
                if (!tag) {
                    tag = document.createElement('div');
                    tag.className = 'modal-session';
                    el.insertBefore(tag, el.firstChild);
                }
                tag.innerHTML = session + ' &middot; live';
            });
        }

        function syncTrend(session) {
            var ts = document.getElementById('trendSession');
            if (!ts) return;
            for (var i = 0; i < ts.options.length; i++) {
                if (ts.options[i].value === session) {
                    if (ts.value !== session) {
                        ts.value = session;
                        ts.dispatchEvent(new Event('change'));
                    }
                    return;
                }
            }
        }

        function setMode(value, live) {
            var frozen = value !== 'live';
            HIDE_SELECTORS.forEach(function (sel) {
                document.querySelectorAll(sel).forEach(function (el) { el.style.display = frozen ? 'none' : ''; });
            });
            var panel = document.getElementById('frozenSessionPanel');
            if (panel) panel.style.display = frozen ? '' : 'none';
            if (frozen) {
                renderFrozen(value);
                var res = finalFor(usableSnaps(), value);
                setSessionInfo(value, true, res ? res.snap.date : '');
                syncTrend(value);
            } else {
                setSessionInfo(live, false, '');
                syncTrend(live);
            }
        }

        function build() {
            var sel = document.getElementById('mainSessionFilter');
            if (!sel) return;
            var snaps = usableSnaps();
            var h = hist();
            // A location whose catalog has never come back populated (0/0 pulls)
            // still knows which session it is being pulled for -- label it, just
            // with nothing to freeze.
            var recorded = h ? (h.snapshots || []).slice().sort(function (a, b) { return a.date < b.date ? -1 : 1; }) : [];
            var live = snaps.length ? snaps[snaps.length - 1].session
                     : (recorded.length ? recorded[recorded.length - 1].session : null);
            if (!live) {
                var grp = document.getElementById('sessionFilterGroup');
                if (grp) grp.style.display = 'none';
                var info = document.querySelector('.session-info');
                if (info) info.style.display = 'none';
                return;
            }
            var names = [];
            snaps.forEach(function (s) { if (names.indexOf(s.session) < 0) names.push(s.session); });

            sel.innerHTML = '';
            var o = document.createElement('option');
            o.value = 'live';
            o.innerHTML = live + ' &mdash; Live (current pull)';
            sel.appendChild(o);
            names.slice().reverse().forEach(function (name) {
                if (name === live) return;
                var res = finalFor(snaps, name);
                if (!res) return;
                var op = document.createElement('option');
                op.value = name;
                op.innerHTML = name + ' &mdash; Final (' + fmtShort(res.snap.date) + ')';
                sel.appendChild(op);
            });

            tagLiveKpis(live);
            setSessionInfo(live, false, '');
            sel.value = 'live';
            sel.addEventListener('change', function () { setMode(sel.value, live); });
        }

        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
        else build();
    })();
    </script>
"""


def _extract_clause(content):
    """Keep whatever 'Includes ...' clause update_dashboard.py last wrote."""
    m = re.search(r'Includes Once-a-Week[^<]*', content)
    return m.group(0).strip() if m else 'Includes Once-a-Week classes'


def _replace_block(content, start_needle, new_block, end_needle='\n        </div>\n'):
    """Replace an 8-space-indented block identified by its opening tag."""
    i = content.find(start_needle)
    if i < 0:
        return content, False
    # back up to the start of the line so indentation is replaced too
    line_start = content.rfind('\n', 0, i) + 1
    j = content.find(end_needle, i)
    if j < 0:
        return content, False
    return content[:line_start] + new_block + content[j + len(end_needle):], True


def _replace_script(content, new_block):
    """Replace the session-toggle <script> block (old Phase-6 or a prior run)."""
    anchor = content.find('var HIDE_SELECTORS')
    if anchor < 0:
        return content, False
    start = content.rfind('<script>', 0, anchor)
    end = content.find('</script>', anchor)
    if start < 0 or end < 0:
        return content, False
    line_start = content.rfind('\n', 0, start) + 1
    nl = content.find('\n', end)
    end = (nl + 1) if nl >= 0 else (end + len('</script>'))
    return content[:line_start] + new_block + content[end:], True


def apply(slug):
    path = os.path.join(REPO, f'{slug}.html')
    if not os.path.exists(path):
        return False, 'no HTML'
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    original = content
    notes = []

    # 1. CSS
    if '.frozen-kpis' not in content:
        content = content.replace('<style>', '<style>' + FREEZE_CSS, 1)
        notes.append('css')

    # 2. session-info line -> id-bearing, JS-filled (insert it if absent)
    clause = _extract_clause(content)
    new_info = SESSION_INFO_TPL.format(clause=clause)
    if 'class="session-info"' in content:
        if 'id="sessionInfoName"' not in content:
            notes.append('session-info')
        content = re.sub(r'<div class="session-info">.*?</div>', lambda m: new_info,
                         content, count=1, flags=re.S)
    else:
        content = re.sub(r'(<div class="extraction-time">.*?</div>\n)',
                         lambda m: m.group(1) + '            ' + new_info + '\n',
                         content, count=1, flags=re.S)
        notes.append('session-info added')

    # 3. Session dropdown -> populated by JS from history
    content = re.sub(r'<select id="mainSessionFilter">.*?</select>',
                     '<select id="mainSessionFilter"></select>', content, count=1, flags=re.S)

    # 4. Panel
    if 'id="frozenSessionPanel"' in content:
        content, ok = _replace_block(content, '<div class="chart-card" id="frozenSessionPanel"', FREEZE_PANEL)
    else:
        content, ok = _replace_block(content, '<div class="chart-card" id="fallPreseasonPanel"', FREEZE_PANEL)
        notes.append('panel')
    if not ok:
        return False, 'panel anchor not found'

    # 5. Toggle script
    content, ok = _replace_script(content, FREEZE_JS)
    if not ok:
        return False, 'script anchor not found'

    if content == original:
        return True, 'unchanged'
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    return True, 'updated' + (' [' + ', '.join(notes) + ']' if notes else '')


def all_slugs():
    from update_all_from_api import SLUGS
    return list(SLUGS.keys())


def main():
    args = sys.argv[1:]
    slugs = all_slugs() if (not args or args[0] == '--all') else args
    ok_n = 0
    for slug in slugs:
        ok, msg = apply(slug)
        print(('  OK  ' if ok else '  ERR ') + f'{slug}: {msg}')
        ok_n += 1 if ok else 0
    print(f'\n{ok_n}/{len(slugs)} dashboards carry {MARKER}')
    if ok_n != len(slugs):
        sys.exit(1)


if __name__ == '__main__':
    main()
