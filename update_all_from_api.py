#!/usr/bin/env python3
"""
Batch dashboard updater for the FOSS API workflow (v8).

Takes a directory of CSVs named foss_api_csv_<slug>.csv (as produced by
foss_api_extract.js -> saved out of the browser) and runs each through
update_dashboard.py against the matching <slug>.html dashboard.

Usage:
    python3 update_all_from_api.py <csv_dir>
    python3 update_all_from_api.py <csv_dir> --only ofallon,northglenn

The slug<->facility map mirrors FACILITIES in foss_api_extract.js.
"""
import os
import sys
import subprocess

# slug -> (facilityId, "Location Name, ST") for the 16 tracked dashboards
SLUGS = {
    'blaine': 'Blaine, MN', 'chanhassen': 'Chanhassen, MN', 'glenview': 'Glenview, IL',
    'highland_park': 'Highland Park, IL', 'lakeview': 'Lakeview, IL', 'libertyville': 'Libertyville, IL',
    'maple_grove': 'Maple Grove, MN', 'niles': 'Niles, IL', 'northglenn': 'Northglenn, CO',
    'ofallon': "O'Fallon, MO", 'richfield': 'Richfield/Edina, MN', 'stlouispark': 'St. Louis Park, MN',
    'sun_prairie': 'Sun Prairie, WI', 'western_springs': 'Western Springs, IL',
    'westminster': 'Westminster, CO', 'woodbury': 'Woodbury, MN',
    'south_barrington': 'South Barrington, IL',
    'savage': 'Savage, MN',
    'st_paul': 'St. Paul, MN',
    'elmwood_park': 'Elmwood Park, IL',
    'plymouth': 'Plymouth, MN',
    'fargo': 'Fargo, ND',
    'ankeny': 'Ankeny, IA',
    'ballwin': 'Ballwin, MO',
    'st_charles': 'St. Charles, MO',
    'vadnais_heights': 'Vadnais Heights, MN',
    'rock_hill': 'Rock Hill, MO',
    'burnsville': 'Burnsville, MN',
    'creve_coeur': 'Creve Coeur, MO',
    'apple_valley': 'Apple Valley, MN',
    'bolingbrook': 'Bolingbrook, IL',
    'castle_rock': 'Castle Rock, CO',
    'lone_tree': 'Lone Tree, CO',
    'parker': 'Parker, CO',
    'otsego': 'Otsego, MN',
}

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    csv_dir = sys.argv[1]
    only = None
    if '--only' in sys.argv:
        only = set(sys.argv[sys.argv.index('--only') + 1].split(','))

    repo = os.path.dirname(os.path.abspath(__file__))
    updated, skipped, errors = [], [], []

    for slug, location in SLUGS.items():
        if only and slug not in only:
            continue
        csv_path = os.path.join(csv_dir, f'foss_api_csv_{slug}.csv')
        camp_path = os.path.join(csv_dir, f'foss_api_campcsv_{slug}.csv')
        html_path = os.path.join(repo, f'{slug}.html')
        if not os.path.exists(csv_path):
            skipped.append((slug, 'no CSV')); continue
        if not os.path.exists(html_path):
            skipped.append((slug, 'no HTML')); continue
        cmd = ['python3', os.path.join(repo, 'update_dashboard.py'),
               csv_path, html_path, '--location', location]
        has_camp = os.path.exists(camp_path)
        if has_camp:
            cmd += ['--camp-csv', camp_path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            updated.append(slug)
            print(f'  OK  {slug}' + ('  (+camps)' if has_camp else ''))
            try:
                from update_index import update_index_card
                iok, imsg = update_index_card(os.path.join(repo, 'index.html'), slug, csv_path, html_path)
                print('       index card: ' + imsg)
            except Exception as e:
                print('       index card ERR: ' + str(e))
        else:
            errors.append((slug, r.stderr.strip()[:200]))
            print(f'  ERR {slug}: {r.stderr.strip()[:200]}')

    print(f'\n{len(updated)} updated, {len(skipped)} skipped, {len(errors)} errors')
    if skipped:
        for s, why in skipped: print(f'  skip {s}: {why}')

    if updated:
        # Re-assert session labelling + the frozen past-session panel. Idempotent,
        # and cheap insurance: when FOSS rolls the catalog to the next season the
        # dashboard body silently becomes that season's data, and without this the
        # page keeps the previous season's hardcoded header (that is how a Fall KPI
        # of 370 ended up over a Summer trend line ending at 707 in Aug 2026).
        try:
            from retrofit_session_freeze import apply as apply_session_freeze
            failures = []
            for slug in updated:
                ok, msg = apply_session_freeze(slug)
                if not ok:
                    failures.append(f'{slug}: {msg}')
            print('\nsession freeze: %d/%d dashboards' % (len(updated) - len(failures), len(updated)))
            for f in failures:
                print('  ERR ' + f)
        except Exception as e:
            print('\nsession freeze ERR: ' + str(e))

        # Regenerate the homepage cards + Enrollment Overview store from the
        # dashboards and history/, so the homepage can't drift from the location
        # pages the way it did through Aug 2026 (see rebuild_index.py).
        try:
            from rebuild_index import rebuild as rebuild_index_cards
            changed, notes = rebuild_index_cards(os.path.join(repo, 'index.html'))
            print('\nhomepage rebuild: %d cards' % len(changed))
            for n in notes:
                print('  ' + n)
        except Exception as e:
            print('\nhomepage rebuild ERR: ' + str(e))

        try:
            from build_agg_chart import build_series, find_core_slugs, update_index_html
            series = build_series(repo)
            core_slugs = find_core_slugs(repo)
            series_core = build_series(repo, only_slugs=set(core_slugs))
            aok, amsg = update_index_html(os.path.join(repo, 'index.html'), series, series_core, core_slugs)
            print('\naggregate chart: ' + amsg if aok else '\naggregate chart ERR: ' + amsg)
        except Exception as e:
            print('\naggregate chart ERR: ' + str(e))

    if errors:
        sys.exit(1)

if __name__ == '__main__':
    main()
