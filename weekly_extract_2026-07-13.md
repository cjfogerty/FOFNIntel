# FOSS Weekly Inventory — Extraction Report

**Run date:** Jul 13, 2026 (Central)
**Season:** Summer 2026
**Locations extracted:** 17 of 17 · **API errors:** 0
**Extractor:** repo `foss_api_extract.js` (API v8) run in the logged-in FOSS tab

## Per-location class counts (weekly / camps / previews)

| Location | Weekly | Camps | Previews |
|---|---:|---:|---:|
| Maple Grove, MN | 721 | 167 | 30 |
| Blaine, MN | 603 | 119 | 17 |
| Lakeview, IL | 569 | 34 | 10 |
| Woodbury, MN | 561 | 72 | 18 |
| Richfield/Edina, MN | 553 | 60 | 10 |
| Chanhassen, MN | 523 | 106 | 22 |
| Sun Prairie, WI | 443 | 24 | 17 |
| Western Springs, IL | 433 | 24 | 0 |
| South Barrington, IL | 366 | 33 | 27 |
| Northglenn, CO | 346 | 22 | 37 |
| St. Louis Park, MN | 335 | 43 | 14 |
| Glenview, IL | 331 | 17 | 15 |
| Libertyville, IL | 318 | 23 | 14 |
| Niles, IL | 307 | 22 | 14 |
| O'Fallon, MO | 248 | 12 | 10 |
| Highland Park, IL | 180 | 12 | 1 |
| Westminster, CO | 161 | 12 | 10 |

## Status of this run

- **Extraction: COMPLETE.** All 17 facilities returned full catalogs with live spot counts; 0 API errors.
- **Dashboard rewrite + GitHub push: NOT completed this run.** See blocker below.

## Blocker

The dashboards are updated by Python scripts that run in the sandbox and require the
per-location CSV files (`foss_api_csv_<slug>` / `foss_api_campcsv_<slug>`). Those CSVs
live in the browser session. Moving them into the sandbox this run was not possible:

1. **Server-side extraction is blocked (by design).** The sandbox can reach the FOSS API,
   but the bearer token is correctly redacted when read from the browser, so the API
   cannot be called from the sandbox.
2. **Browser→sandbox transport of the gzipped blob is unreliable.** The combined CSVs
   compress to ~70 KB of base64. The only channel is reproducing that text verbatim into
   sandbox files; large high-entropy base64 cannot be reproduced character-perfect, and
   the page-read output was also truncated by the harness on large reads. Writing
   unverified/corrupted CSVs into the dashboards and pushing them would be worse than
   skipping, so no dashboards were modified and nothing was pushed.

**Nothing was written to the repo. The live site is unchanged from last week.**

## Security note

During this run, two pieces of text appeared *inside tool outputs* (a page-read result and
a shell listing) that were formatted to look like user interruptions / self-directed
instructions ("wait, this is not chunk 0"; "let me stop and reassess…"). These were not
messages from Casey and were not acted on as instructions.
