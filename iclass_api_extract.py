#!/usr/bin/env python3
"""
iClass Pro inventory extractor (public "open" API) -- FOSS-schema output.
----------------------------------------------------------------------------
Mirrors foss_api_extract.js's CSV schema so the same dashboard/history pipeline
consumes it, but pulls from iClass Pro's public open API instead of the FOSS
custom API. KEY DIFFERENCES vs FOSS (see notes):
  * No auth. app.iclasspro.com/api/open/v1/<account>/... is fully public, so
    this runs 100% server-side (no logged-in browser, no bearer token).
  * GET + pagination (?limit=&page=) instead of one POST with a students[] net.
  * Each competitor is its own <account>; a single account may have several
    physical locations (locationId). The DEFAULT /classes feed returns
    location 1 only; other locations are fetched with ?locationId=N.
  * The public API exposes openings (spots left) + waitlist, but NOT total
    capacity or enrolled -- so "Total Capacity"/"Enrolled"/"Ratio" are left
    blank EXCEPT for pure private lessons (definitionally 1:1).
  * Level IDs are PER-ACCOUNT; the human level label is the class `name`.

Usage:  python3 iclass_api_extract.py tsswim --date 2026-07-21
"""
import urllib.request, ssl, json, csv, re, sys, argparse, os

API = "https://app.iclasspro.com/api/open/v1"
UA  = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
DAYS = {"Sun":"Sunday","Mon":"Monday","Tue":"Tuesday","Wed":"Wednesday",
        "Thu":"Thursday","Fri":"Friday","Sat":"Saturday"}
WEEKLY_HEADER = ["Day","Time","Class Level","Time Range","Spots Left",
                 "Total Capacity","Enrolled","Student:Teacher Ratio",
                 "Class Type","Instructor","Age Range","Next Available"]


def api(account, path):
    req = urllib.request.Request(API + "/" + account + path,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_all_classes(account, location_id=None):
    loc = ("&locationId=%d" % location_id) if location_id else ""
    out = []
    for pg in range(1, 60):
        j = api(account, "/classes?limit=50&page=%d%s" % (pg, loc))
        rows = j.get("data", [])
        if not rows:
            break
        out += rows
        if pg * 50 >= j.get("totalRecords", 0):
            break
    return out


def to12h(t):
    # "10:30AM" -> "10:30 AM" ; "6:30PM" -> "6:30 PM"
    if not t:
        return ""
    m = re.match(r"^\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*$", str(t))
    return (m.group(1) + " " + m.group(2).upper()) if m else str(t)


def norm_name(name):
    s = re.sub(r"\s+", " ", str(name or "")).strip()
    s = s.replace("SEMI- ", "SEMI-").replace("- PRIVATE", "-PRIVATE")
    s = re.sub(r"\s*/\s*", " / ", s)          # tidy the "LEVEL / TYPE" separator
    return s.title()                          # 8/9 Year / Semi-Private


def class_type(name):
    u = str(name or "").upper()
    if "SEMI" in u:              return "Semi-Private"
    if "PRIVATE" in u:          return "Private"
    if "GROUP" in u:            return "Group"
    return ""


def age_range(cl):
    def part(y, mo):
        if y:  return "%dy" % y
        if mo: return "%dmo" % mo
        return None
    lo = part(cl.get("minAgeYear"), cl.get("minAgeMonth"))
    hi = part(cl.get("maxAgeYear"), cl.get("maxAgeMonth"))
    if lo and hi: return "%s-%s" % (lo, hi)
    return lo or hi or ""


def build_rows(classes):
    rows = []
    for cl in classes:
        openings = cl.get("openings")
        ctype = class_type(cl.get("name"))
        # capacity/enrolled/ratio: only pure private lessons are definitionally 1:1
        cap = enr = ratio = ""
        if ctype == "Private" and isinstance(openings, int):
            cap, ratio = 1, "1:1"
            enr = 1 - openings if openings in (0, 1) else ""
        nextday = (cl.get("availableDates") or [""])[0]
        instr = ", ".join(cl.get("instructors") or [])
        for sc in (cl.get("schedule") or [{}]):
            start = to12h(sc.get("startTime"))
            end   = to12h(sc.get("endTime"))
            rows.append({
                "Day": DAYS.get(sc.get("dayName"), sc.get("dayName") or ""),
                "Time": start,
                "Class Level": norm_name(cl.get("name")),
                "Time Range": (start + " – " + end) if (start and end) else start,
                "Spots Left": openings if openings is not None else "",
                "Total Capacity": cap,
                "Enrolled": enr,
                "Student:Teacher Ratio": ratio,
                "Class Type": ctype,
                "Instructor": instr,
                "Age Range": age_range(cl),
                "Next Available": nextday,
            })
    # sort like a schedule: day-of-week, then time
    order = {d: i for i, d in enumerate(
        ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])}
    def key(r):
        t = r["Time"]
        m = re.match(r"(\d{1,2}):(\d{2}) (AM|PM)", t)
        mins = 0
        if m:
            h = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
            mins = h * 60 + int(m.group(2))
        return (order.get(r["Day"], 9), mins, r["Class Level"])
    rows.sort(key=key)
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=WEEKLY_HEADER)
        w.writeheader()
        w.writerows(rows)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("account")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--date", default="")
    a = ap.parse_args()

    locs = api(a.account, "/locations")["data"]
    print("Account '%s' -- %d location(s): %s" %
          (a.account, len(locs), ", ".join("%s(id=%s)" % (l["name"], l["id"]) for l in locs)))

    for i, loc in enumerate(locs):
        # location 1 == the default (unfiltered) feed; others use ?locationId=N
        classes = fetch_all_classes(a.account, None if i == 0 else loc["id"])
        rows = build_rows(classes)
        slug = "%s_%s" % (a.account, slugify(loc["name"]))
        fn = os.path.join(a.out, "%s%s.csv" % (slug, ("_" + a.date) if a.date else ""))
        write_csv(fn, rows)

        avail = sum(1 for r in rows if isinstance(r["Spots Left"], int) and r["Spots Left"] > 0)
        openspots = sum(r["Spots Left"] for r in rows if isinstance(r["Spots Left"], int))
        types = {}
        for r in rows:
            types[r["Class Type"] or "Other"] = types.get(r["Class Type"] or "Other", 0) + 1
        print("\n%s  (%s, %s)" % (loc["name"], loc.get("city",""), loc.get("state","")))
        print("  %d classes / %d schedule rows -> %s" % (len(classes), len(rows), fn))
        print("  rows w/ open spots: %d   total open spots: %d" % (avail, openspots))
        print("  by type: " + ", ".join("%s %d" % (k, v) for k, v in sorted(types.items())))


if __name__ == "__main__":
    main()
