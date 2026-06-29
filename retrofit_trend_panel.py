#!/usr/bin/env python3
"""
Retrofit the "Enrollment Over Time" trend panel into the production
<slug>.html dashboards, in place. Idempotent: skips dashboards that already
have the panel. Each dashboard loads its history from history/<slug>.js
(written by update_dashboard.py on every pull, seeded by backfill_history.py).

Usage:
    python3 retrofit_trend_panel.py --all
    python3 retrofit_trend_panel.py northglenn ofallon
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.abspath(__file__))

TREND_STYLE = """
        /* === Enrollment-over-time trend panel === */
        .trend-card { margin-bottom: 20px; }
        .trend-controls { display: flex; gap: 15px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 14px; }
        .trend-note { font-size: 12px; color: #718096; padding-bottom: 8px; }
        .trend-chart-wrap { position: relative; height: 380px; }
        .trend-legend-key { font-size: 12px; color: #4a5568; margin-top: 10px; }
        .trend-legend-key span { margin-right: 18px; white-space: nowrap; }
        #trendMoversTable { min-width: 0; font-size: 12px; }
        #trendMoversTable td, #trendMoversTable th { padding: 7px 10px; text-align: left; border-bottom: 1px solid #edf2f7; }
        #trendMoversTable th { background: #f7fafc; font-weight: 600; }
        #trendMoversTable td.num { text-align: right; font-variant-numeric: tabular-nums; }
        .delta-up { color: #2f855a; font-weight: 600; }
        .delta-down { color: #c53030; font-weight: 600; }
        .sim-badge { display: inline-block; background: #fefcbf; color: #744210; font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-left: 8px; vertical-align: middle; }
"""

TREND_HTML = """
        <div class="chart-card trend-card" id="trendCard">
            <div class="chart-title">\U0001F4C8 Enrollment Over Time <span style="font-weight:400;color:#718096;">— one point per data pull</span><span class="sim-badge" id="trendSimBadge" style="display:none;">includes simulated future pulls (mockup demo)</span></div>
            <div class="trend-controls">
                <div class="filter-group">
                    <label>Session</label>
                    <select id="trendSession"></select>
                </div>
                <div class="filter-group">
                    <label>Metric</label>
                    <select id="trendMetric">
                        <option value="enrolled">Enrolled</option>
                        <option value="util">Utilization %</option>
                        <option value="open">Open Spots</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>Group Lines By</label>
                    <select id="trendGroupBy">
                        <option value="total">Total</option>
                        <option value="category">Curriculum Category</option>
                        <option value="level">Specific Level</option>
                        <option value="day">Day of Week</option>
                        <option value="band">Time of Day</option>
                    </select>
                </div>
                <div class="trend-note">Respects the Day / Category / Level filters above.</div>
            </div>
            <div class="trend-chart-wrap"><canvas id="trendChart"></canvas></div>
            <div class="trend-legend-key">
                <span>&#9679;&#9472;&#9472; in-session pull</span>
                <span style="letter-spacing:1px;">&#9679;&middot;&middot;&middot;&middot; pre-season pull (enrollment open, session not started)</span>
                <span style="opacity:0.55;">&#9472;&#9472;&#9472; last value held (session still operating, no newer pull)</span>
                <span id="trendSimKey" style="display:none;">&#9472; &#9472; simulated demo pull</span>
            </div>
            <div class="chart-title" style="font-size:14px;margin-top:22px;margin-bottom:10px;">Biggest movers since previous pull <span style="font-weight:400;color:#718096;" id="trendMoversRange"></span></div>
            <table id="trendMoversTable"></table>
        </div>
"""

TREND_JS = r"""
    <script>
    (function () {
        if (typeof ENROLLMENT_HISTORY === 'undefined') return;
        const H = ENROLLMENT_HISTORY;
        const DAY_ORDER = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
        const BAND_ORDER = ["Morning (before 12 PM)","Afternoon (12–4 PM)","Evening (4 PM+)","Other"];
        const CAT_PREFIXES = [["Preview","Previews"],["Backfloat Baby","Backfloat Baby"],["Little","Littles"],["Middle","Middles"],["Big","Bigs"],["10+","10+"],["Adult","Adults"],["Private Lesson","Privates"]];
        const PALETTE = ['#5b4a9f','#4299e1','#48bb78','#ed8936','#9f7aea','#f56565','#38b2ac','#d69e2e','#667eea','#fc8181','#68d391','#f6ad55','#b794f4','#4fd1c5','#e53e3e','#7f9cf5'];
        const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

        function catOf(level) {
            for (const [p, c] of CAT_PREFIXES) if (level.startsWith(p)) return c;
            return "Other";
        }
        function bandOf(hhmm) {
            const h = parseInt(hhmm, 10);
            if (isNaN(h)) return "Other";
            if (h < 12) return "Morning (before 12 PM)";
            if (h < 16) return "Afternoon (12–4 PM)";
            return "Evening (4 PM+)";
        }
        function to12h(hhmm) {
            const p = hhmm.split(':');
            let h = parseInt(p[0], 10);
            const ap = h >= 12 ? 'PM' : 'AM';
            h = h % 12; if (h === 0) h = 12;
            return h + ':' + p[1] + ' ' + ap;
        }
        function fmtDate(iso) {
            const p = iso.split('-');
            return MONTHS[parseInt(p[1], 10) - 1] + ' ' + parseInt(p[2], 10);
        }
        function alpha(hex, a) {
            const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
            return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
        }

        // Session calendar: name -> {start, end, catalog_from}
        const SESSION_META = {};
        (H.sessions || []).forEach(s => { SESSION_META[s.name] = s; });

        // Pre-parse "Day|HH:MM|Level" slot keys once.
        const SNAPS = H.snapshots.map(s => {
            const rows = [];
            for (const k in s.slots) {
                const i = k.indexOf('|'), j = k.indexOf('|', i + 1);
                const day = k.slice(0, i), time = k.slice(i + 1, j), level = k.slice(j + 1);
                rows.push({ key: k, day, time, level, cat: catOf(level), band: bandOf(time), e: s.slots[k][0], c: s.slots[k][1] });
            }
            return { date: s.date, label: s.label, session: s.session, simulated: !!s.simulated, rows };
        });
        SNAPS.sort((a, b) => a.date < b.date ? -1 : 1);
        const GLOBAL_MAX_DATE = SNAPS.length ? SNAPS[SNAPS.length - 1].date : '';
        if (SNAPS.some(s => s.simulated)) {
            document.getElementById('trendSimBadge').style.display = '';
            document.getElementById('trendSimKey').style.display = '';
        }

        // Session selector: newest session first (default); "All Sessions" last.
        const sessionNames = [];
        SNAPS.forEach(s => { if (!sessionNames.includes(s.session)) sessionNames.push(s.session); });
        const sessSel = document.getElementById('trendSession');
        sessionNames.slice().reverse().forEach(name => {
            const o = document.createElement('option');
            o.value = name; o.textContent = name;
            sessSel.appendChild(o);
        });
        const all = document.createElement('option');
        all.value = ''; all.textContent = 'All Sessions';
        sessSel.appendChild(all);

        function groupOf(r, by) {
            switch (by) {
                case 'category': return r.cat;
                case 'level': return r.level;
                case 'day': return r.day;
                case 'band': return r.band;
                default: return 'Total';
            }
        }
        function sortGroups(names, by) {
            if (by === 'day') return names.sort((a, b) => DAY_ORDER.indexOf(a) - DAY_ORDER.indexOf(b));
            if (by === 'band') return names.sort((a, b) => BAND_ORDER.indexOf(a) - BAND_ORDER.indexOf(b));
            return names.sort();
        }
        function passesFilters(r, f) {
            if (f.day && r.day !== f.day) return false;
            if (f.category && r.cat !== f.category) return false;
            if (f.levels && f.levels.length > 0 && !f.levels.includes(r.level)) return false;
            return true;
        }

        // Vertical "session starts" markers, set per render.
        let sessionMarkers = [];
        const sessionStartPlugin = {
            id: 'sessionStartMarker',
            afterDatasetsDraw(chart) {
                const x = chart.scales && chart.scales.x, area = chart.chartArea, ctx = chart.ctx;
                if (!x || !area) return;   // chart mid-destroy/rebuild
                sessionMarkers.forEach(m => {
                    const px = x.getPixelForValue(m.index);
                    if (!(px >= area.left - 1 && px <= area.right + 1)) return;
                    ctx.save();
                    ctx.strokeStyle = 'rgba(91,74,159,0.5)';
                    ctx.setLineDash([4, 4]);
                    ctx.beginPath(); ctx.moveTo(px, area.top); ctx.lineTo(px, area.bottom); ctx.stroke();
                    ctx.setLineDash([]);
                    ctx.fillStyle = '#5b4a9f';
                    ctx.font = '600 11px -apple-system, BlinkMacSystemFont, sans-serif';
                    ctx.textAlign = 'left';
                    ctx.fillText(m.label, px + 5, area.top + 12);
                    ctx.restore();
                });
            }
        };

        let trendChart = null;

        window.updateTrendChart = function () {
            const sessFilter = sessSel.value;
            const metric = document.getElementById('trendMetric').value;
            const by = document.getElementById('trendGroupBy').value;
            const f = (typeof currentFilters !== 'undefined') ? currentFilters : { day: '', category: '', levels: [] };
            const inViewSessions = sessFilter ? [sessFilter] : sessionNames;

            // Pulls per session, each pull pre-aggregated into group cells.
            const pullsBySession = {};
            const groupNameSet = new Set();
            inViewSessions.forEach(name => {
                pullsBySession[name] = SNAPS.filter(s => s.session === name).map(s => {
                    const cells = new Map();
                    s.rows.forEach(r => {
                        if (!passesFilters(r, f)) return;
                        const g = groupOf(r, by);
                        groupNameSet.add(g);
                        const cell = cells.get(g) || { e: 0, c: 0 };
                        cell.e += r.e; cell.c += r.c;
                        cells.set(g, cell);
                    });
                    return { date: s.date, label: s.label, simulated: s.simulated, cells };
                });
            });

            // Unified date axis: every pull date, plus each session's start date
            // (marker) and carry-forward terminator (= session end, capped at the
            // newest pull anywhere — sessions still operating "hold" their last
            // value until they actually end).
            const dateSet = new Set();
            inViewSessions.forEach(name => {
                const pulls = pullsBySession[name];
                if (!pulls.length) return;
                pulls.forEach(p => dateSet.add(p.date));
                const meta = SESSION_META[name];
                if (!meta) return;
                const lastPull = pulls[pulls.length - 1].date;
                const carryEnd = meta.end <= GLOBAL_MAX_DATE ? meta.end : GLOBAL_MAX_DATE;
                if (carryEnd > lastPull) dateSet.add(carryEnd);
                if (meta.start >= pulls[0].date && meta.start <= GLOBAL_MAX_DATE) dateSet.add(meta.start);
            });
            const axisDates = [...dateSet].sort();
            const labels = axisDates.map(fmtDate);

            sessionMarkers = [];
            inViewSessions.forEach(name => {
                const meta = SESSION_META[name];
                if (!meta) return;
                const idx = axisDates.indexOf(meta.start);
                if (idx >= 0) sessionMarkers.push({ index: idx, label: name + ' session starts' });
            });

            const metricLabel = metric === 'util' ? 'Utilization %' : (metric === 'open' ? 'Open Spots' : 'Enrolled');
            const suffix = metric === 'util' ? '%' : '';
            function val(cell) {
                if (!cell || cell.c === 0) return null;
                if (metric === 'util') return +(cell.e / cell.c * 100).toFixed(1);
                if (metric === 'open') return cell.c - cell.e;
                return cell.e;
            }

            const groupNames = sortGroups([...groupNameSet], by);
            const datasets = [];
            inViewSessions.forEach((name, sIdx) => {
                const pulls = pullsBySession[name];
                if (!pulls.length) return;
                const meta = SESSION_META[name] || { start: '0000', end: '9999' };
                const lastPull = pulls[pulls.length - 1];
                const carryEnd = meta.end <= GLOBAL_MAX_DATE ? meta.end : GLOBAL_MAX_DATE;
                const isNewest = name === inViewSessions[inViewSessions.length - 1];
                const pullByDate = new Map(pulls.map(p => [p.date, p]));

                groupNames.forEach(g => {
                    const color = PALETTE[groupNames.indexOf(g) % PALETTE.length];
                    const lineColor = isNewest ? color : alpha(color, 0.65);
                    const data = [], kinds = [], radii = [];
                    axisDates.forEach(d => {
                        const pull = pullByDate.get(d);
                        if (pull) {
                            data.push(val(pull.cells.get(g)));
                            kinds.push(pull.simulated ? 'sim' : 'pull');
                            radii.push(3);
                        } else if (d > lastPull.date && d <= carryEnd) {
                            data.push(val(lastPull.cells.get(g)));   // held value
                            kinds.push('held');
                            radii.push(0);
                        } else {
                            data.push(null);                          // outside this session
                            kinds.push(null);
                            radii.push(0);
                        }
                    });
                    if (!data.some(v => v !== null)) return;
                    datasets.push({
                        label: g + (sessFilter ? '' : ' — ' + name),
                        data,
                        borderColor: lineColor,
                        backgroundColor: lineColor,
                        tension: 0.25,
                        spanGaps: true,
                        pointRadius: radii,
                        pointHoverRadius: radii.map(r => r ? 5 : 3),
                        borderWidth: 2,
                        segment: {
                            borderDash: ctx => {
                                // pre-season: segment ends before the session starts
                                if (axisDates[ctx.p1DataIndex] < meta.start) return [2, 4];
                                if (kinds[ctx.p1DataIndex] === 'sim') return [6, 4];
                                return undefined;
                            },
                            borderColor: ctx => kinds[ctx.p1DataIndex] === 'held' ? alpha(color, 0.35) : undefined
                        },
                        _kinds: kinds,
                        _session: name,
                        _meta: meta,
                        _heldFrom: lastPull.label,
                        _group: g
                    });
                });
            });

            // Destroy whatever chart currently owns the canvas (not just our
            // reference) so a failed render can never wedge the canvas.
            const canvas = document.getElementById('trendChart');
            const existing = Chart.getChart(canvas);
            if (existing) existing.destroy();
            trendChart = new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: { labels, datasets },
                plugins: [sessionStartPlugin],
                options: {
                    responsive: true, maintainAspectRatio: false, animation: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'bottom', display: datasets.length > 1, labels: { boxWidth: 12, font: { size: 11 } } },
                        tooltip: {
                            callbacks: {
                                title: items => {
                                    if (!items.length) return '';
                                    const d = axisDates[items[0].dataIndex];
                                    return fmtDate(d) + ', ' + d.slice(0, 4);
                                },
                                label: ctx => {
                                    const v = ctx.parsed.y;
                                    if (v === null) return null;
                                    const ds = ctx.dataset;
                                    const k = ds._kinds[ctx.dataIndex];
                                    const d = axisDates[ctx.dataIndex];
                                    let note = '';
                                    if (k === 'held') {
                                        note = '  — held from ' + ds._heldFrom + ' pull (' + ds._session + ' still operating)';
                                    } else {
                                        if (d < ds._meta.start) note = '  — pre-season (' + ds._session + ' starts ' + fmtDate(ds._meta.start) + ')';
                                        if (k === 'sim') note += '  [simulated]';
                                        // delta vs previous actual pull of this line
                                        for (let j = ctx.dataIndex - 1; j >= 0; j--) {
                                            if (ds._kinds[j] === 'pull' || ds._kinds[j] === 'sim') {
                                                const prev = ds.data[j];
                                                if (prev !== null) {
                                                    const delta = +(v - prev).toFixed(1);
                                                    note = '  (' + (delta >= 0 ? '+' : '') + delta + suffix + ' vs prior pull)' + note;
                                                }
                                                break;
                                            }
                                        }
                                    }
                                    return ds.label + ': ' + v + suffix + note;
                                }
                            }
                        }
                    },
                    scales: { y: { beginAtZero: true, title: { display: true, text: metricLabel } } }
                }
            });
            updateMovers(pullsBySession, inViewSessions, f);
        };

        function updateMovers(pullsBySession, inViewSessions, f) {
            const tbl = document.getElementById('trendMoversTable');
            const rangeEl = document.getElementById('trendMoversRange');
            // Compare the two most recent pulls of the newest in-view session.
            let pulls = [];
            let sessName = '';
            for (let i = inViewSessions.length - 1; i >= 0; i--) {
                if (pullsBySession[inViewSessions[i]].length >= 2) {
                    sessName = inViewSessions[i];
                    pulls = pullsBySession[sessName];
                    break;
                }
            }
            if (pulls.length < 2) {
                tbl.innerHTML = '<tbody><tr><td style="color:#718096;">Need at least two pulls in a session to show movers — they will appear after the next scheduled extraction.</td></tr></tbody>';
                rangeEl.textContent = '';
                return;
            }
            const prevSnap = SNAPS.filter(s => s.session === sessName)[pulls.length - 2];
            const nowSnap = SNAPS.filter(s => s.session === sessName)[pulls.length - 1];
            rangeEl.textContent = '(' + sessName + ': ' + prevSnap.label + ' → ' + nowSnap.label + (nowSnap.simulated ? ', simulated' : '') + ')';
            const prevMap = new Map();
            prevSnap.rows.forEach(r => { if (passesFilters(r, f)) prevMap.set(r.key, r); });
            const movers = [];
            nowSnap.rows.forEach(r => {
                if (!passesFilters(r, f)) return;
                const p = prevMap.get(r.key);
                const pe = p ? p.e : 0;
                if (r.e !== pe) movers.push({ r, pe, delta: r.e - pe });
            });
            movers.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
            const top = movers.slice(0, 8);
            let html = '<thead><tr><th>Day</th><th>Time</th><th>Level</th><th style="text-align:right;">Was</th><th style="text-align:right;">Now</th><th style="text-align:right;">Δ</th><th style="text-align:right;">Open Now</th></tr></thead><tbody>';
            if (top.length === 0) {
                html += '<tr><td colspan="7" style="color:#718096;">No slot-level changes between the last two pulls (with current filters).</td></tr>';
            }
            top.forEach(m => {
                const cls = m.delta > 0 ? 'delta-up' : 'delta-down';
                const sign = m.delta > 0 ? '+' : '';
                html += '<tr><td>' + m.r.day + '</td><td>' + to12h(m.r.time) + '</td><td>' + m.r.level + '</td>' +
                        '<td class="num">' + m.pe + '</td><td class="num">' + m.r.e + '</td>' +
                        '<td class="num ' + cls + '">' + sign + m.delta + '</td><td class="num">' + (m.r.c - m.r.e) + '</td></tr>';
            });
            html += '</tbody>';
            tbl.innerHTML = html;
        }

        // Re-render when trend controls change…
        ['trendSession', 'trendMetric', 'trendGroupBy'].forEach(id =>
            document.getElementById(id).addEventListener('change', updateTrendChart));

        // …and when the dashboard's own filters change (wrap the existing hooks).
        if (typeof applyFilters === 'function') {
            const origApply = applyFilters;
            applyFilters = function () { origApply(); updateTrendChart(); };
        }
        if (typeof switchView === 'function') {
            const origSwitch = switchView;
            switchView = function (v) {
                origSwitch(v);
                document.getElementById('trendCard').style.display = (v === 'camps') ? 'none' : '';
            };
        }

        updateTrendChart();
    })();
    </script>
"""

def build(slug):
    path = os.path.join(REPO, f'{slug}.html')
    with open(path, encoding='utf-8') as f:
        html = f.read()

    if 'id="trendCard"' in html:
        print(f'  skip {slug}: trend panel already present')
        return False

    loader = f'\n    <script src="history/{slug}.js"></script>'
    html, n1 = re.subn(r'(<script src="https://cdn\.jsdelivr\.net/npm/chart\.js[^"]*"></script>)',
                       lambda m: m.group(1) + loader, html, count=1)
    html, n2 = re.subn(r'(\n\s*</style>)', lambda m: '\n' + TREND_STYLE + m.group(1), html, count=1)
    html, n3 = re.subn(r'(\n\s*<div class="chart-grid">)',
                       lambda m: '\n' + TREND_HTML + m.group(1), html, count=1)
    html, n4 = re.subn(r'(\n\s*</body>)', lambda m: '\n' + TREND_JS + m.group(1), html, count=1)

    if not all([n1, n2, n3, n4]):
        raise SystemExit(f'{slug}: anchor not found: loader={n1} css={n2} html={n3} js={n4}')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  OK   {slug}')
    return True

SLUGS = ['blaine', 'chanhassen', 'glenview', 'highland_park', 'lakeview',
         'libertyville', 'maple_grove', 'niles', 'northglenn', 'ofallon',
         'richfield', 'stlouispark', 'sun_prairie', 'western_springs',
         'westminster', 'woodbury',
         'south_barrington']

if __name__ == '__main__':
    args = sys.argv[1:]
    slugs = SLUGS if '--all' in args else [a for a in args if a in SLUGS]
    if not slugs:
        print(__doc__)
        sys.exit(1)
    done = sum(build(s) for s in slugs)
    print(f'{done} dashboards updated, {len(slugs) - done} skipped')
