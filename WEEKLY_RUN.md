# FOSS Inventory Update — Runbook

Canonical steps for the extraction + dashboard update. (June 2026: the
pipeline now also records an enrollment-history snapshot per location on every
run — no extra steps, but the commit must include `history/`.)

**Automated runs (Aug 2026):** `.github/workflows/refresh-foss.yml` runs this
whole pipeline daily via GitHub Actions (13:00 UTC), with no dependency on a
local machine or browser — `foss_api_extract.py` logs in directly against the
`Authentication/Login` API and drives the same extraction as step 1 below.
The manual steps below (`foss_api_extract.js` via Claude in Chrome) are still
useful for one-off/ad-hoc runs or debugging, but are no longer how the regular
refresh happens.

## Steps

1. **Extract** (Claude in Chrome, logged-in FOSS account tab):
   - Inject `foss_api_extract.js`, then run `await window.fossApiExtractAll()`.
   - Save each `foss_api_csv_<slug>.csv` (and `foss_api_campcsv_<slug>.csv`
     where present) from localStorage into one directory.
   - The run log now reports a `previews` count per facility — if previews
     appear, they are labeled `Preview <Level>` and show up as their own
     levels under the "Previews" category on the dashboards.

2. **Update dashboards + history**:
   ```
   python3 update_all_from_api.py <csv_dir>
   ```
   Each location's `update_dashboard.py` run refreshes the dashboard HTML AND
   appends today's snapshot to `history/<slug>.json` + `history/<slug>.js`
   (idempotent: re-running the same day overwrites that day's snapshot).

3. **Commit & push** — must include the history files:
   ```
   git add -A          # NOT just *.html — history/ must ride along
   git commit -m "Weekly inventory update (API v8) - <date>"
   git push
   ```

## Quarterly maintenance

When a new session opens for enrollment (Fall 2026, etc.), add one line to the
`SESSIONS` list in **append_history.py** (and mirror it in
**backfill_history.py**): name, start, end, and `catalog_from` = the
enrollment-open date. The dashboards' "Enrollment Over Time" panel uses this
calendar for its session selector, pre-season (dotted) styling, and
held-value carry-forward at session changeover.

To close out the outgoing session, give its `SESSIONS` entry a `final_pull`
date (the last pull that counts). Nothing is recorded under it after that date,
pulls fall through to the next session on the calendar, and the dashboards
label it "Final (<date>)" instead of "Live". Fall 2026 is final as of
2026-10-11; Winter 2027 (weekly Nov 30 - Mar 12, pre-enrollment Oct 27) is the
session now being pulled.

Set the season being pulled with `FOSS_SEASON_ID` in
**.github/workflows/refresh-foss.yml** (101 = Winter 2026-27), and keep the
fallback in **foss_api_extract.py** matching.

Between quarters FOSS returns only a few Preview lessons per location.
`update_all_from_api.py` skips any location whose pull has no regular classes,
so a closed or not-yet-open catalog never overwrites the dashboard or history.
Expect "catalog not open" skips and no daily commit until the new catalog opens.

**Pre-enrollment targets are automatic.** `build_targets.py` (run by
`update_all_from_api.py` after every pull) sets each location's target for the
session being pulled: 80-85% of the seats it has posted, checked 5 weeks after
the session starts. It's recomputed from each pull while the session is in
pre-enrollment (as FOSS opens or pulls classes) and locked from the first pull
on or after the session start. Fall 2026's targets were hand-built Jul 29 and
are left as they are.

Also bump `FALLBACK_SEASON_ID` in **foss_api_extract.js** to the new season's
id (seasonIds increment by 1 each quarter — Winter/Spring/Summer/Fall). The
API does NOT auto-correct a stale seasonId; it just returns 0 classes for an
ended season, which silently records zeros into `history/` if unnoticed. The
script now probes forward and warns in the console if it has to self-heal
(2026-08-05 incident: 99/Summer was stale, 100/Fall was current), but don't
rely on that — fix the constant.

## Session rollover: what the dashboards do on their own

The dashboard body (KPI cards, charts, tables) is always the session the
extractor pulled that morning — when FOSS rolls the catalog, the body follows
it. `retrofit_session_freeze.py` (run automatically at the end of
`update_all_from_api.py`) makes the page say so: the header line, the KPI
"Fall 2026 · live" tag, and the Session dropdown are all filled in the browser
from `history/<slug>.js`, so a rollover relabels itself with no code change.

**Past sessions are frozen, never re-derived.** Once FOSS closes a session to
new enrollment it stops returning that session's classes, so a later pull comes
back short — Westminster's Summer seats fell 581 → 484 between the 2026-07-16
and 2026-07-19 pulls because 23 of its 24 Sunday classes dropped out of the
catalog. Selecting a past session shows its last **complete** pull out of
`history/`: totals, day and category breakdowns, and a note if a truncated
later pull was skipped. `TRUNC_RATIO` (0.95) in `retrofit_session_freeze.py`
and `build_agg_chart.py` is what flags a pull as truncated; the homepage
aggregate carries the last complete totals forward for those dates so the
tail of each session doesn't show a fake dip.

Symptom this prevents: a Fall KPI (370) sitting above a Summer trend line
ending at 707, with the header still claiming "Summer 2026 Session" — the
state every dashboard was in from the 2026-08-05 rollover until 2026-08-17.

## Homepage (index.html)

Three things on the homepage are generated, and all three run automatically at
the end of `update_all_from_api.py`:

- `update_index.py` — incremental per-location card patch (runs per slug).
- `rebuild_index.py` — regenerates every card **and** the Enrollment Overview
  store (`<script id="fossEnrollData">`) from each `<slug>.html` plus
  `history/`. Run it standalone any time the homepage looks out of step with a
  location page: `python3 rebuild_index.py`.
- `build_agg_chart.py` — the Total Enrollment Over Time blob.

`update_index.py` patches cards with regexes and used to `return` on an
unchanged card *before* writing the chart store, so any card whose markup
didn't match (every location added after the original cohort — their
hand-authored Fall panels say "seats posted" / "Target: N–M by Oct 5") silently
stopped updating the chart too. On 2026-08-17 Ballwin's dashboard read 714
while the homepage chart still had its 2026-08-05 value of 520; 17 of 35
locations were stale, and the cohort's *Summer* tab was being overwritten with
Fall numbers. `rebuild_index.py` builds from data instead, so it can't drift;
cards are emitted live-session-first so the incremental patcher still lands on
the live panel between rebuilds.

## One-time tools (already run, kept for reference)

- `backfill_history.py --all` — rebuilds `history/` from git history.
- `retrofit_trend_panel.py --all` — injects the trend panel into dashboards
  (idempotent; needed only for newly added locations).

## Adding a new location

1. Add facilityId→slug to `FACILITIES` in `foss_api_extract.js` and
   slug→location in `SLUGS` in `update_all_from_api.py` (+ `backfill_history.py`,
   `retrofit_trend_panel.py`).
2. Create `<slug>.html` from an existing dashboard (e.g. copy `northglenn.html`).
3. Run the extraction + `update_all_from_api.py`, then
   `python3 retrofit_trend_panel.py <slug>`.
