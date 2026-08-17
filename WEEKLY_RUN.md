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
