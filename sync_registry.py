#!/usr/bin/env python3
"""
sync_registry.py -- fold the latest jackrabbit_enrollment.csv figures back into
jr_orgs.json, so the registry always shows what is measured and what is not.

Run after pull_jackrabbit.py. Safe to re-run; it overwrites the `measured` block
on each matching location and leaves everything else alone.
"""
import csv
import json
import os

REG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jr_orgs.json")
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jackrabbit_enrollment.csv")

d = json.load(open(REG))
meas = {r["slug"]: r for r in csv.DictReader(open(SRC))}

n = 0
for org in d["orgs"]:
    for loc in org.get("locations", []):
        m = meas.get(loc.get("slug"))
        if not m:
            continue
        loc["status"] = "live"
        loc["loc_filter"] = m["loc"]
        loc["measured"] = {
            "est_enrolled": int(m["est_enrolled"]),
            "derived_capacity": int(m["derived_capacity"]),
            "est_utilization_pct": float(m["est_utilization_pct"]),
            "classes": int(m["classes"]),
            "classes_waitlisted": int(m["waitlisted"]),
            "capacity_basis": m["capacity_basis"],
            "observed_date": m["observed_date"],
        }
        n += 1

# Correction: EMBTRY carries live classes in org 543485 even though the site is
# gone from British's public website roster. Absent-from-website does NOT imply
# closed -- the Jackrabbit feed is the better test.
for org in d["orgs"]:
    for loc in org.get("locations", []):
        if loc.get("slug") == "bss_charlotte_tryon" and "measured" in loc:
            loc["note"] = ("CORRECTED 2026-08-29: previously flagged roster_absent from "
                           "the website. The Jackrabbit feed shows it still running "
                           "(4 classes, 12 enrolled) -- winding down, not closed. "
                           "Treat website absence as a prompt to check the feed, "
                           "never as proof of closure.")

with open(REG, "w") as f:
    json.dump(d, f, indent=2)
print(f"synced {n} locations into {REG}")

unmeasured = [l["lma_site_name"] for o in d["orgs"] for l in o.get("locations", [])
              if "measured" not in l and l.get("lma_site_name")]
print(f"still unmeasured: {len(unmeasured)}")
for u in unmeasured:
    print("  ", u)
