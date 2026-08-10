#!/usr/bin/env python3
"""
FOSS API Extractor (v8-py -- headless port of foss_api_extract.js for GitHub Actions).

foss_api_extract.js runs this same logic inside a logged-in browser tab, reading
the session's bearer token out of localStorage. This version logs in directly via
the same Authentication/Login JSON endpoint the web app uses (email+password,
token returned straight in the response body -- no browser needed) and then makes
the identical SelectClasses/v2 calls, so the output CSVs are byte-for-byte
compatible with what foss_api_extract.js produces.

Keep FALLBACK_SEASON_ID, STUDENTS, FACILITIES, and LEVEL_NAMES in sync with
foss_api_extract.js -- see the quarterly-maintenance section in WEEKLY_RUN.md.

Env vars required:
  FOSS_EMAIL     -- FOSS Family Accounts login email
  FOSS_PASSWORD  -- FOSS Family Accounts login password

Usage:
    python3 foss_api_extract.py <out_dir>
    python3 foss_api_extract.py <out_dir> --only ofallon,northglenn
"""
import argparse
import csv
import io
import os
import sys
from datetime import datetime

import requests

API_BASE = "https://api-account.fossswimschool.com/api"

# seasonId: SelectClasses/v2 does NOT auto-correct a stale seasonId -- it just
# echoes back whatever is sent, even for an ended season (returns 0 classes).
# get_current_season_id() below is a probe, not real discovery: bump this
# constant every quarter (see WEEKLY_RUN.md) or the pipeline will silently
# record zeros. Keep in sync with FALLBACK_SEASON_ID in foss_api_extract.js.
FALLBACK_SEASON_ID = 100  # Fall 2026 (sessionYear 2026)

# Standing student/level array: casts the widest net across all distinct levels
# so SelectClasses/v2 returns the FULL catalog per facility. Mirrors STUDENTS
# in foss_api_extract.js exactly.
STUDENTS = [
    {"studentId": 1223932, "levelId": 1}, {"studentId": 1223939, "levelId": 2}, {"studentId": 937269, "levelId": 3},
    {"studentId": 1223472, "levelId": 5}, {"studentId": 1223924, "levelId": 6}, {"studentId": 1223933, "levelId": 6},
    {"studentId": 1223475, "levelId": 6}, {"studentId": 657438, "levelId": 11}, {"studentId": 1223941, "levelId": 11},
    {"studentId": 1223940, "levelId": 12}, {"studentId": 1223934, "levelId": 10}, {"studentId": 326679, "levelId": 11},
    {"studentId": 1223485, "levelId": 18}, {"studentId": 1223925, "levelId": 14}, {"studentId": 1223947, "levelId": 17},
    {"studentId": 1223935, "levelId": 15}, {"studentId": 1223928, "levelId": 34}, {"studentId": 1223946, "levelId": 19},
    {"studentId": 1223936, "levelId": 33}, {"studentId": 289154, "levelId": 31}, {"studentId": 1223943, "levelId": 32},
    {"studentId": 1223942, "levelId": 30},
    {"studentId": 1223472, "levelId": 4}, {"studentId": 1223472, "levelId": 7}, {"studentId": 1223472, "levelId": 8},
    {"studentId": 1223472, "levelId": 9}, {"studentId": 1223472, "levelId": 13}, {"studentId": 1223472, "levelId": 16},
]

# facilityId -> dashboard slug. Mirrors FACILITIES in foss_api_extract.js.
FACILITIES = {
    5: "blaine", 2: "chanhassen", 31: "glenview", 8: "highland_park", 10: "lakeview",
    9: "libertyville", 3: "maple_grove", 12: "niles", 35: "northglenn", 18: "ofallon",
    23: "richfield", 1: "stlouispark", 22: "sun_prairie", 29: "western_springs",
    34: "westminster", 6: "woodbury", 11: "south_barrington", 4: "savage", 7: "st_paul",
    15: "elmwood_park", 16: "plymouth", 19: "fargo", 20: "ankeny", 21: "ballwin",
    24: "st_charles", 25: "vadnais_heights", 26: "rock_hill", 27: "burnsville",
    28: "creve_coeur", 30: "apple_valley", 32: "bolingbrook", 36: "castle_rock",
    37: "lone_tree", 38: "parker", 39: "otsego",
}

# levelId -> display name. Mirrors LEVEL_NAMES in foss_api_extract.js.
LEVEL_NAMES = {
    1: "Backfloat Baby 1 (BB1)", 2: "Backfloat Baby 2 (BB2)", 3: "Backfloat Baby 3 (BB3)", 4: "Backfloat Baby 4 (BB4)",
    5: "Little 1 (L1)", 6: "Little 2 (L2)", 7: "Little 3 (L3)", 8: "Little 4 (L4)",
    9: "Middle 1 (M1)", 10: "Middle 2 (M2)", 11: "Middle 3 (M3)", 12: "Middle 4 (M4)", 13: "Middle 5 (M5)",
    14: "Big 1 (B1)", 15: "Big 2 (B2)", 16: "Big 3 (B3)", 17: "Big 4 (B4)", 18: "Big 5 (B5)", 19: "Big 6 (B6)",
    33: "10+1 (10+1)", 34: "10+2 (10+2)",
    30: "Adult 1 (A1)", 31: "Adult 2 (A2)", 32: "Adult 3 (A3)",
}

WEEKLY_HEADER = ["Day", "Time", "Class Level", "Time Range", "Spots Left", "Total Capacity", "Enrolled", "Student:Teacher Ratio"]
CAMP_HEADER = ["Camp Name", "Date Range", "Days", "Time", "Class Level", "Time Range", "Spots Left", "Total Capacity", "Enrolled", "Student:Teacher Ratio"]


def login(email, password):
    r = requests.post(f"{API_BASE}/Authentication/Login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    token = r.json().get("token")
    if not token:
        sys.exit("Login succeeded but response had no 'token' field")
    return token


def api_post(path, token, body):
    r = requests.post(
        f"{API_BASE}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def to12h(hms):
    h, m = str(hms).split(":")[:2]
    h, m = int(h), int(m)
    ap = "PM" if h >= 12 else "AM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {ap}"


def fmt_date(iso):
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(str(iso).split(".")[0])
    except ValueError:
        parts = str(iso).split("T")[0].split("-")
        return f"{parts[1]}/{parts[2]}" if len(parts) >= 3 else str(iso)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[d.month - 1]} {d.day}"


def is_preview(cls):
    s = " ".join(str(cls.get(k) or "") for k in ("sessionTypeCode", "sessionTypeCategory", "sessionName")).lower()
    return "preview" in s


def level_label(cls):
    preview = is_preview(cls)
    if cls.get("accessTypeCode") == "P":
        return "Preview Private (PRE)" if preview else "Private Lesson (PV)"
    nm = LEVEL_NAMES.get(cls.get("levelId"), f"Level {cls.get('levelId')}")
    return f"Preview {nm}" if preview else nm


def is_camp(cls):
    if cls.get("sessionTypeId") == 2:
        return True
    code = str(cls.get("sessionTypeCode") or "").lower()
    cat = str(cls.get("sessionTypeCategory") or "").lower()
    return "camp" in code or "camp" in cat


def camp_days(cls):
    days = cls.get("campWeekDays")
    if isinstance(days, list) and days:
        return "/".join(str(d)[:3] for d in days)
    return cls.get("classDay") or ""


def class_to_row(cls):
    total = cls.get("totalSlots")
    open_ = cls.get("availableSlots")
    enrolled = (total - open_) if (total is not None and open_ is not None) else ""
    ratio = f"{total}:1" if total is not None else ""
    tr = f"{to12h(cls.get('classStartTime'))} – {to12h(cls.get('classEndTime'))}"
    return {
        "Day": cls.get("classDay") or "",
        "Time": to12h(cls.get("classStartTime")),
        "Class Level": level_label(cls),
        "Time Range": tr,
        "Spots Left": open_ if open_ is not None else "",
        "Total Capacity": total if total is not None else "",
        "Enrolled": enrolled,
        "Student:Teacher Ratio": ratio,
    }


def camp_to_row(cls):
    total = cls.get("totalSlots")
    open_ = cls.get("availableSlots")
    enrolled = (total - open_) if (total is not None and open_ is not None) else ""
    ratio = f"{total}:1" if total is not None else ""
    tr = f"{to12h(cls.get('classStartTime'))} – {to12h(cls.get('classEndTime'))}"
    start, end = cls.get("startDate"), cls.get("endDate")
    date_range = f"{fmt_date(start)} – {fmt_date(end)}" if (start or end) else ""
    return {
        "Camp Name": cls.get("sessionName") or "4 Week Camp",
        "Date Range": date_range,
        "Days": camp_days(cls),
        "Time": to12h(cls.get("classStartTime")),
        "Class Level": level_label(cls),
        "Time Range": tr,
        "Spots Left": open_ if open_ is not None else "",
        "Total Capacity": total if total is not None else "",
        "Enrolled": enrolled,
        "Student:Teacher Ratio": ratio,
    }


def rows_to_csv(rows, header):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def probe_season_id(token, facility_id, season_id):
    body = {"facilityId": facility_id, "seasonId": season_id, "students": STUDENTS[:1]}
    resp = api_post("/Classes/SelectClasses/v2", token, body)
    types = resp.get("availableSessionTypes") or []
    weekly_open = any(t.get("sessionTypeCode") == "Once a Week" for t in types)
    return {
        "sid": resp.get("seasonId") or season_id,
        "quarter": resp.get("sessionQuarter"),
        "year": resp.get("sessionYear"),
        "weeklyOpen": weekly_open,
    }


def get_current_season_id(token, facility_id):
    try:
        r = probe_season_id(token, facility_id, FALLBACK_SEASON_ID)
    except Exception:
        return FALLBACK_SEASON_ID
    if r["weeklyOpen"]:
        print(f"[FOSS-API] Using seasonId {r['sid']} ({r['quarter']} {r['year']})")
        return r["sid"]
    print(f"[FOSS-API] WARNING: FALLBACK_SEASON_ID {FALLBACK_SEASON_ID} ({r['quarter']} {r['year']}) "
          "has weekly registration closed -- probing forward. Update FALLBACK_SEASON_ID!")
    for candidate in range(FALLBACK_SEASON_ID + 1, FALLBACK_SEASON_ID + 5):
        try:
            r2 = probe_season_id(token, facility_id, candidate)
        except Exception:
            continue
        if r2["weeklyOpen"]:
            print(f"[FOSS-API] Auto-recovered: using seasonId {r2['sid']} ({r2['quarter']} {r2['year']}) instead.")
            return r2["sid"]
    return FALLBACK_SEASON_ID


def extract_facility(token, facility_id, season_id):
    body = {"facilityId": facility_id, "seasonId": season_id, "students": STUDENTS}
    resp = api_post("/Classes/SelectClasses/v2", token, body)
    seen = set()
    weekly_rows, camp_rows = [], []
    preview_count = 0
    for student in resp.get("students") or []:
        for cls in student.get("classes") or []:
            cid = cls.get("classId")
            if cid in seen:
                continue
            seen.add(cid)
            if is_preview(cls):
                preview_count += 1
            if is_camp(cls):
                camp_rows.append(camp_to_row(cls))
            else:
                weekly_rows.append(class_to_row(cls))
    return {
        "facilityName": resp.get("facilityName"),
        "sessionQuarter": resp.get("sessionQuarter"),
        "sessionYear": resp.get("sessionYear"),
        "weeklyCount": len(weekly_rows),
        "campCount": len(camp_rows),
        "previewCount": preview_count,
        "csv": rows_to_csv(weekly_rows, WEEKLY_HEADER),
        "campCsv": rows_to_csv(camp_rows, CAMP_HEADER) if camp_rows else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--only", default=None, help="comma-separated slugs to limit extraction to")
    a = ap.parse_args()

    email = os.environ.get("FOSS_EMAIL")
    password = os.environ.get("FOSS_PASSWORD")
    if not email or not password:
        sys.exit("FOSS_EMAIL and FOSS_PASSWORD must be set in the environment")

    os.makedirs(a.out_dir, exist_ok=True)

    only = set(a.only.split(",")) if a.only else None
    ids = [fid for fid, slug in FACILITIES.items() if not only or slug in only]
    if not ids:
        sys.exit("--only matched no known facility slugs")

    token = login(email, password)
    print(f"[FOSS-API] Logged in as {email}")

    season_id = get_current_season_id(token, ids[0])

    errors = []
    camp_locs = []
    for fid in ids:
        slug = FACILITIES[fid]
        try:
            res = extract_facility(token, fid, season_id)
        except Exception as e:
            errors.append((slug, str(e)))
            print(f"[FOSS-API] ERROR facility {fid} ({slug}): {e}")
            continue

        with open(os.path.join(a.out_dir, f"foss_api_csv_{slug}.csv"), "w", newline="") as f:
            f.write(res["csv"])
        if res["campCsv"]:
            with open(os.path.join(a.out_dir, f"foss_api_campcsv_{slug}.csv"), "w", newline="") as f:
                f.write(res["campCsv"])
            camp_locs.append(slug)

        print(f"[FOSS-API] {slug}: {res['weeklyCount']} weekly + {res['campCount']} camp classes, "
              f"{res['previewCount']} previews ({res['sessionQuarter']} {res['sessionYear']})")

    ok_count = len(ids) - len(errors)
    print(f"[FOSS-API] DONE. {ok_count}/{len(ids)} facilities, camps at {len(camp_locs)} "
          f"({', '.join(camp_locs) or 'none'}), {len(errors)} errors.")
    if errors:
        for slug, err in errors:
            print(f"  ERR {slug}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
