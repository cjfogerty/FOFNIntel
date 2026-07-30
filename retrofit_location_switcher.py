#!/usr/bin/env python3
"""
Retrofit a "Location" dropdown into the production FOSS <slug>.html dashboards,
in place, so a user can jump straight to any other FOSS location from the page
itself instead of going back through index.html. Idempotent: skips dashboards
that already have the switcher. Mirrors the retrofit_trend_panel.py pattern --
re-run this any time update_dashboard.py regenerates a dashboard, since that
script doesn't know about the switcher and won't preserve it on its own.

The location list is read straight from index.html's "FOSS Locations" cards
(the same 35 slug/name pairs shown on the hub), so it can't drift out of sync
with what's actually live -- re-run add_location.py + this script (in that
order) after adding a new location.

Usage:
    python3 retrofit_location_switcher.py --all
    python3 retrofit_location_switcher.py northglenn ofallon
"""
import argparse
import json
import os
import re

REPO = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(REPO, "index.html")

SWITCHER_MARKER = 'id="locationSwitcher"'


def load_locations():
    with open(INDEX) as f:
        html = f.read()
    start = html.index("FOSS Locations")
    end = html.index("Competitive Intel")
    block = html[start:end]
    pairs = re.findall(r'<a class="card foss-card" href="([^"]+)">\s*<h2>([^<]+)</h2>', block)
    return [{"slug": href[:-5], "name": name.strip()} for href, name in pairs]


def switcher_html(locations, current_slug):
    return (
        '<div class="filter-group" id="locationSwitcherGroup">\n'
        '                    <label>Location</label>\n'
        f'                    <select id="locationSwitcher"></select>\n'
        "                </div>\n                "
    )


def switcher_script(locations, current_slug):
    return f"""
    <script>
    (function() {{
        var LOCATIONS = {json.dumps(locations)};
        var CURRENT = {json.dumps(current_slug)};
        var sel = document.getElementById('locationSwitcher');
        if (!sel) return;
        LOCATIONS.forEach(function(loc) {{
            var opt = document.createElement('option');
            opt.value = loc.slug;
            opt.textContent = loc.name;
            if (loc.slug === CURRENT) opt.selected = true;
            sel.appendChild(opt);
        }});
        sel.addEventListener('change', function() {{
            if (sel.value !== CURRENT) window.location.href = sel.value + '.html';
        }});
    }})();
    </script>
"""


def retrofit(slug, locations):
    path = os.path.join(REPO, slug + ".html")
    if not os.path.exists(path):
        print(f"skip {slug}: no such file")
        return False
    with open(path) as f:
        html = f.read()
    if SWITCHER_MARKER in html:
        print(f"skip {slug}: already has switcher")
        return False
    if '<div class="filters">' not in html:
        print(f"skip {slug}: no <div class=\"filters\"> anchor found")
        return False

    html = html.replace(
        '<div class="filters">',
        '<div class="filters">\n                ' + switcher_html(locations, slug),
        1,
    )
    html = html.replace("</body>", switcher_script(locations, slug) + "</body>", 1)

    with open(path, "w") as f:
        f.write(html)
    print(f"retrofit {slug}: OK")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="location slugs (e.g. ofallon); omit with --all")
    ap.add_argument("--all", action="store_true", help="retrofit every FOSS location on index.html")
    a = ap.parse_args()

    locations = load_locations()
    targets = [loc["slug"] for loc in locations] if a.all else a.slugs
    if not targets:
        raise SystemExit("pass slugs or --all")

    changed = sum(retrofit(slug, locations) for slug in targets)
    print(f"\n{changed}/{len(targets)} dashboards updated")


if __name__ == "__main__":
    main()
