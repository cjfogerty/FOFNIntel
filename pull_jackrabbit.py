#!/usr/bin/env python3
"""
pull_jackrabbit.py -- enrollment extraction for Jackrabbit-Class competitors
from Jackrabbit's PUBLIC OpeningsJS feed. No parent account, no login, no
Playwright.

WHY THIS REPLACES THE LOGIN PIPELINE
------------------------------------
barron_extract.py logs in because `GetClassesForEnroll` requires a parent
session. It does -- that endpoint 302s to /Login unauthenticated. But it is not
the only source. Jackrabbit also serves an unauthenticated class-listing feed
that orgs embed on their own websites:

    https://app.jackrabbitclass.com/jr3.0/Openings/OpeningsJS?OrgID=<org>&loc=<code>

It carries the same fields the dashboards use -- class, day, time, openings,
tuition, start date, waitlist status. Verified HTTP 200 with data on all 13
orgs tried (Bear Paddle, 3x Barron, 9x British) on 2026-08-29.

WHAT IS AND IS NOT PUBLISHED
----------------------------
`openings` is published. ENROLLED AND CAPACITY ARE NOT. Capacity is derived,
exactly as in the validated iClassPro method, so every enrolled figure here is
an ESTIMATE and must be labelled as one downstream:

    enrolled = derived_capacity - openings

Capacity per class, in priority order:
  1. private  -> 1 seat, semi-private -> 2 seats (from class name / tuition tier)
  2. brand-published instructor ratio where one exists
     (British publishes 4:1 for survival classes, 6:1 for Tadpole)
  3. max openings ever observed for that level across the whole org -- an empty
     class exposes its full size. Used as both a floor and a cross-check on (2).

For British, (2) and (3) agree independently at 4, which is the strongest
calibration in the set. For Bear Paddle no public ratio was found, so capacity
rests on (3) alone and is correspondingly weaker -- flagged per row.

LOCATION SPLITTING DIFFERS BY BRAND
-----------------------------------
British encodes the location in the class name ("Starfish - 24KM - 3 Tue"), so
an org feed can be split locally. Bear Paddle does NOT -- its class names are
generic ("Teddy Advanced") and the org feed carries no location column, so each
location must be fetched with its own &loc= code. Codes are NOT derivable from
the location name (Kildeer is DPK, for Deer Park). Unknown codes return a clean
empty table, which makes probing safe and unambiguous.

OUTPUT
------
  history/<slug>.json   dated snapshot appended, same shape as the FOSS history
                        files, so pipeline/refresh_offplatform.py picks it up
  jackrabbit_enrollment.csv  flat summary for direct LMA import

Usage:  python3 pull_jackrabbit.py [--dry-run] [--slug SLUG]
"""
import argparse
import collections
import csv
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "history")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
FEED = "https://app.jackrabbitclass.com/jr3.0/Openings/OpeningsJS?OrgID={org}"

# ---------------------------------------------------------------- registry --
# loc: Jackrabbit &loc= code (Bear Paddle). name_code: token inside the class
# name (British). Exactly one of the two is used per row.
SITES = [
    # --- Bear Paddle, org 499027, one national org, split by &loc= ----------
    dict(brand="Bear Paddle", org="499027", loc="BMD", slug="bearpaddle_bloomingdale",
         location="Bloomingdale, IL", lma="Bear Paddle Swim School - Bloomingdale"),
    dict(brand="Bear Paddle", org="499027", loc="WTN", slug="bearpaddle_wheaton",
         location="Wheaton, IL", lma="Bear Paddle Swim School - Wheaton"),
    dict(brand="Bear Paddle", org="499027", loc="WDR", slug="bearpaddle_woodridge",
         location="Woodridge, IL", lma="Bear Paddle Swim School - Woodridge"),
    dict(brand="Bear Paddle", org="499027", loc="NLS", slug="bearpaddle_niles",
         location="Niles, IL", lma="Bear Paddle Swim School - Niles"),
    dict(brand="Bear Paddle", org="499027", loc="AUR", slug="bearpaddle_aurora",
         location="Aurora, IL", lma="Bear Paddle Swim School - Aurora"),
    dict(brand="Bear Paddle", org="499027", loc="DPK", slug="bearpaddle_kildeer",
         location="Kildeer, IL", lma="Bear Paddle Swim School - Kildeer",
         note="Bear Paddle calls this Deer Park (DPK); the LMA calls it Kildeer."),
    dict(brand="Bear Paddle", org="499027", loc="MRL", slug="bearpaddle_marlton",
         location="Marlton, NJ", lma="Bear Paddle Swim School - Marlton"),

    # --- British, split by the code embedded in the class name -------------
    # Every British org encodes its site in the class name, but the format varies
    # per org: bare token (ALDRGT), dash-delimited (- MRTWST -), or bracketed
    # ([24-PISCAT]). name_code is matched on word boundaries, so a list handles
    # sites that use more than one spelling.
    dict(brand="British", org="548999", name_code=["24KM"], slug="bss_kearny_mesa",
         location="San Diego, CA", lma="British Swim School - 24 HR Fitness Kearny Mesa"),
    dict(brand="British", org="548999", name_code=["UTC"], slug="bss_utc",
         location="San Diego, CA", lma="British Swim School - 24 Hour Fitness UTC"),
    dict(brand="British", org="548999", name_code=["LAFWTC"], slug="bss_woodbury",
         location="Irvine, CA", lma="British Swim School - LA Fitness Woodbury Town Center"),
    dict(brand="British", org="548999", name_code=["24MKTP"], slug="bss_jamboree",
         location="Irvine, CA", lma="British Swim School - LA Fitness Jamboree",
         note="TENTATIVE MAPPING. The org's Irvine site is 24 Hour Fitness Irvine "
              "Marketplace (24MKTP), by Jamboree Rd. The LMA carries 'LA Fitness "
              "Jamboree' at 2880 Michelle Dr -- a different brand and address. "
              "Likely the same programme after a facility move, but CONFIRM before "
              "trusting the join."),

    dict(brand="British", org="529897", name_code=["MRTWST"], slug="bss_westminster",
         location="Westminster, CO", lma="British Swim School - Denver Marriott Westminster"),

    dict(brand="British", org="543485", name_code=["LAFBAL"], slug="bss_ballantyne",
         location="Charlotte, NC", lma="British Swim School - LA Fitness Ballantyne"),
    dict(brand="British", org="543485", name_code=["HIEMAT"], slug="bss_matthews",
         location="Matthews, NC", lma="British Swim School - Matthews"),
    dict(brand="British", org="543485", name_code=["EMBTRY"], slug="bss_charlotte_tryon",
         location="Charlotte, NC", lma="British Swim School - Embassy Suites Charlotte S. Tryon",
         note="Absent from the public website roster but STILL RUNNING in Jackrabbit "
              "(4 classes). The roster_absent flag in jr_orgs.json was wrong for this "
              "site -- it is winding down, not closed."),

    dict(brand="British", org="521398", name_code=["LA59"], slug="bss_naperville_59",
         location="Naperville, IL", lma="British Swim School - LA Fitness Naperville Rte 59"),
    dict(brand="British", org="521398", name_code=["LAFAH"], slug="bss_arlington_heights",
         location="Arlington Heights, IL", lma="British Swim School - LA Fitness Arlington Heights"),
    dict(brand="British", org="521398", name_code=["HICS"], slug="bss_carol_stream",
         location="Carol Stream, IL", lma="British Swim School - Holiday Inn & Suites Carol Stream"),

    dict(brand="British", org="514856", name_code=["SK"], slug="bss_skokie",
         location="Skokie, IL", lma="British Swim School - Skokie"),

    dict(brand="British", org="529182", name_code=["LAF-NB"], slug="bss_north_brunswick",
         location="North Brunswick, NJ", lma="British Swim School - LA Fitness North Brunswick"),

    dict(brand="British", org="516158", name_code=["VILLAP"], slug="bss_villa_park",
         location="Villa Park, IL", lma="British Swim School - Oakbrook Terrace/Villa Park"),

    dict(brand="British", org="526635", name_code=["JAG"], slug="bss_richboro",
         location="Richboro, PA", lma="British Swim School - JAG Richboro"),
    dict(brand="British", org="526635", name_code=["LAB"], slug="bss_bensalem",
         location="Bensalem, PA", lma="British Swim School - LA Fitness Bensalem"),
    dict(brand="British", org="526635", name_code=["KL"], slug="bss_kleinlife",
         location="Philadelphia, PA", lma="British Swim School - KleinLife Northeast Philadelphia"),
    dict(brand="British", org="526635", name_code=["AB", "ABGTN"], slug="bss_jenkintown",
         location="Jenkintown, PA", lma="British Swim School - Abington Club Jenkintown"),
    dict(brand="British", org="526635", name_code=["EF"], slug="bss_voorhees",
         location="Voorhees Township, NJ",
         lma="British Swim School - Echelon Health and Fitness Voorhees"),

    dict(brand="British", org="548617", name_code=["EOSOCT"], slug="bss_ocotillo",
         location="Chandler, AZ", lma="British Swim School - EoS Fitness Ocotillo Gilbert"),
    dict(brand="British", org="548617", name_code=["EOSRAY"], slug="bss_chandler_ray",
         location="Chandler, AZ", lma="British Swim School - EoS Fitness Chandler Ray and Rural"),
    dict(brand="British", org="548617", name_code=["LIFE"], slug="bss_gilbert_lauren",
         location="Gilbert, AZ", lma="British Swim School - Lauren's Institute Gilbert"),
]

# Brand-published instructor ratios. British states 4:1 for survival classes and
# 6:1 for Tadpole / Stroke Development on its own site. No public ratio was
# found for Bear Paddle, so it falls through to observed-max only.
PUBLISHED_RATIO = {"British": {"_default": 4, "Tadpole": 6}}

# Only real lesson sessions count. This MUST be a blocklist, not an allowlist:
# session strings are wildly inconsistent between orgs -- Bear Paddle uses
# "Weekly Classes - Mornings", British Flatirons uses "Regular Monthly", Barron
# uses "Dance Classes". An allowlist requiring "class" silently returned zero
# rows for every org that does not use that word.
SESSION_SKIP = re.compile(
    r"template|camp|swim\s*meet|clinic|family\s*swim|open\s*swim|party|event",
    re.I)


# ------------------------------------------------------------------ parsing --
class _T(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.cur, self.cell, self.grab = [], [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cur = []
        if tag in ("td", "th"):
            self.cell, self.grab = [], True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.cur.append(" ".join("".join(self.cell).split()))
            self.grab = False
        if tag == "tr" and self.cur:
            self.rows.append(self.cur)
            self.cur = []

    def handle_data(self, d):
        if self.grab:
            self.cell.append(d)


def parse_feed(text):
    chunks = re.findall(r"'((?:[^'\\]|\\.)*)'", text)
    doc = "".join(c.replace("\\'", "'").replace('\\"', '"').replace("\\/", "/")
                  for c in chunks)
    doc = html.unescape(doc)
    doc = re.sub(r"<style.*?</style>", "", doc, flags=re.S | re.I)
    doc = re.sub(r"<script.*?</script>", "", doc, flags=re.S | re.I)
    p = _T()
    p.feed(doc)
    rows = [r for r in p.rows if any(c.strip() for c in r)]

    hdr = None
    for r in rows:
        # The header must be found via cell 1 ("Class"). Cell 0 is "Register"
        # on the header AND on every enrollable data row -- keying off cell 0
        # silently drops every class that has openings and yields a fake 100%.
        if len(r) > 1 and r[1].strip().lower() == "class":
            hdr = [c.strip().lower().replace(" ", "_") for c in r]
            break
    if hdr is None:
        return []
    out = []
    for r in rows:
        if len(r) != len(hdr):
            continue
        if r[1].strip().lower() == "class":
            continue
        out.append(dict(zip(hdr, r)))
    return out


def fetch(org, loc=None):
    url = FEED.format(org=org) + (f"&loc={loc}" if loc else "")
    res = subprocess.run(["curl", "-s", "-m", "60", "-A", UA, url],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"curl failed for org={org} loc={loc}")
    return res.stdout


def num(s, default=0.0):
    m = re.search(r"[\d.]+", (s or "").replace(",", ""))
    return float(m.group()) if m else default


def level_of(name):
    return re.split(r"\s+-\s+", name)[0].strip()


def kind_of(row):
    n = row.get("class", "").lower()
    t = num(row.get("tuition"))
    if "semi" in n:
        return "semi"
    if "private" in n:
        return "private"
    if t >= 450:
        return "private"
    return "group"


def lessons_only(rows):
    out = []
    for r in rows:
        if SESSION_SKIP.search(r.get("session", "")):
            continue
        out.append(r)
    return out


# ------------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slug", help="only this slug")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    sites = [s for s in SITES if not args.slug or s["slug"] == args.slug]

    # Calibrate capacity per (brand, level) from the widest sample available:
    # the full org feed, where an empty class exposes its true size.
    orgcache, maxopen = {}, collections.defaultdict(int)
    for s in {(x["brand"], x["org"]) for x in sites}:
        brand, org = s
        raw = parse_feed(fetch(org))
        orgcache[org] = raw
        for r in lessons_only(raw):
            r["_kind"] = kind_of(r)
            if r["_kind"] == "group":
                lvl = level_of(r.get("class", ""))
                maxopen[(brand, lvl)] = max(maxopen[(brand, lvl)],
                                            int(num(r.get("openings"))))

    def capacity(brand, r):
        k = r["_kind"]
        if k == "private":
            return 1
        if k == "semi":
            return 2
        lvl = level_of(r.get("class", ""))
        pub = PUBLISHED_RATIO.get(brand, {})
        base = pub.get(lvl, pub.get("_default", 0))
        return max(base, maxopen.get((brand, lvl), 0), 1)

    summary = []
    for s in sites:
        if s.get("loc"):
            rows = parse_feed(fetch(s["org"], s["loc"]))
        else:
            codes = s["name_code"]
            if isinstance(codes, str):
                codes = [codes]
            pat = re.compile("|".join(rf"\b{re.escape(c)}\b" for c in codes))
            rows = [r for r in orgcache[s["org"]] if pat.search(r.get("class", ""))]
        rows = lessons_only(rows)
        for r in rows:
            r["_kind"] = kind_of(r)

        if not rows:
            print(f"  !! {s['slug']}: no classes returned -- skipped")
            continue

        cap = op = 0
        waitlisted = 0
        slots = {}
        for r in rows:
            c = max(capacity(s["brand"], r), int(num(r.get("openings"))))
            o = int(num(r.get("openings")))
            cap += c
            op += o
            if r.get("register", "").strip().lower() == "waitlist":
                waitlisted += 1
            key = f"{r.get('days','?')}|{r.get('times','?')}|{level_of(r.get('class',''))}"
            prev = slots.get(key, [0, 0])
            slots[key] = [prev[0] + (c - o), prev[1] + c]

        enrolled = cap - op
        util = round(enrolled / cap * 100, 1) if cap else 0.0
        calib = "published_ratio+observed_max" if s["brand"] in PUBLISHED_RATIO \
            else "observed_max_only"

        summary.append(dict(
            brand=s["brand"], slug=s["slug"], location=s["location"],
            lma_site_name=s["lma"], classes=len(rows), waitlisted=waitlisted,
            openings=op, derived_capacity=cap, est_enrolled=enrolled,
            est_utilization_pct=util, capacity_basis=calib,
            org=s["org"], loc=s.get("loc") or s.get("name_code"),
            observed_date=today))
        print(f"  {s['slug']:<28} {len(rows):>4} cls  cap {cap:>4}  "
              f"open {op:>4}  enr {enrolled:>4}  util {util:>5.1f}%  [{calib}]")

        if args.dry_run:
            continue

        path = os.path.join(HIST, f"{s['slug']}.json")
        if os.path.exists(path):
            doc = json.load(open(path))
        else:
            doc = {"location": s["location"], "slug": s["slug"],
                   "sessions": [{"name": "Continuous enrollment",
                                 "start": today, "end": "2099-12-31",
                                 "catalog_from": today}],
                   "snapshots": [], "targets": {}}
        doc["snapshots"] = [x for x in doc.get("snapshots", []) if x.get("date") != today]
        doc["snapshots"].append({
            "date": today,
            "ts": dt.datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "label": dt.date.today().strftime("%b %-d"),
            "session": "Continuous enrollment",
            "totals": {"enrolled": enrolled, "capacity": cap, "utilization": util},
            "slots": slots,
            "source": "jackrabbit_public_openings",
            "capacity_basis": calib,
        })
        doc["snapshots"].sort(key=lambda x: x["date"])
        os.makedirs(HIST, exist_ok=True)
        with open(path, "w") as f:
            json.dump(doc, f, indent=1)

    if summary and not args.dry_run:
        out = os.path.join(HERE, "jackrabbit_enrollment.csv")
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0].keys()))
            w.writeheader()
            w.writerows(summary)
        print(f"\nwrote {out}  ({len(summary)} sites)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
