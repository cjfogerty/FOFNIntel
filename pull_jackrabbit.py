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

    # --- added 2026-08-31 from the national-index sweep (LMA CAVEATS 4p) -----
    # Both orgs were found on britishswimschool.com/locations/us/, verified live
    # against their own openings feeds, and their site tokens read off the raw
    # class names rather than guessed.
    dict(brand="British", org="548998", name_code=["DIVDRA"], slug="bss_lehi_draper",
         location="Draper, UT", lma="British Swim School - Lehi-Draper",
         note="Dive Addicts Draper. DIVDRA carries 67 of the org's 70 classes; the "
              "remaining 3 are untagged makeups. Single-pool territory."),
    dict(brand="British", org="526069", name_code=["Buckhead"], slug="bss_lenox_buckhead",
         location="Atlanta, GA", lma="British Swim School - LA Fitness Lenox/Buckhead",
         note="This org tags the site at the END of the class name in plain words "
              "-- 'Adult 1 4:30 Monday Buckhead' -- not as an uppercase code. Its "
              "other tokens are Roswell and Onelife."),

    # --- Swim Atlanta, added 2026-08-31 -------------------------------------
    # The brand was on file as unreadable ("books through its own /find/ paths
    # on its own domain"). It does -- and those paths hand off to Jackrabbit.
    # One org per physical location, like Barron, so no name_code is needed.
    # Org IDs read from each location page's regv2.asp?id= registration link
    # and joined to the LMA on an exact street-address match.
    # No public instructor ratio was found, so capacity rests on observed-max
    # only, the weaker of the two calibrations -- same basis as Bear Paddle.
    # HELD OUT pending a session-selection rule. Swim Atlanta does not run
    # continuous enrolment like British and Bear Paddle -- it sells MONTHLY
    # SESSIONS, and the feed lists every session from August through December
    # 2026 at once. Summing them counts the same physical slot five times and
    # drags utilisation down with months nobody has booked yet: Roswell reads
    # 12.2% and Johns Creek 31.3% on that basis, which is not what either site
    # is doing. Measuring them needs the CURRENT session only. Uncomment once
    # that rule exists; the org IDs and address joins below are verified.
    #   Roswell     org 551530, 57 classes, 795 Old Roswell Rd -- exact join
    #   Johns Creek org 539092, 327 classes, 4050 Johns Creek Pkwy -- exact join
    # dict(brand="Swim Atlanta", org="551530", name_code=None, slug="swimatl_roswell",
    #      location="Roswell, GA", lma="Swim Atlanta Roswell"),
    # dict(brand="Swim Atlanta", org="539092", name_code=None, slug="swimatl_johns_creek",
    #      location="Suwanee, GA", lma="Swim Atlanta Johns Creek"),
    # NOT added: East Cobb (LMA has 2111 Old Canton Rd; the nearest Swim Atlanta
    # page, /midway, is 5059 Post Road -- a different address, so no join) and
    # Georgia Tech (the /gatech page carries no regv2 link, so no org). Do not
    # guess either; that is the trap that produced the LA Fitness Jamboree hold.

    dict(brand="British", org="547058", name_code=["LAF"], slug="bss_shelby",
         location="Shelby Township, MI", lma="British Swim School - LA Fitness Shelby",
         note="All 91 of the org's classes carry LAF and the macomb-utica territory "
              "lists one pool, so this is a single-site org. Org verified live "
              "2026-08-31 from the national-index sweep."),

    # Hudson Waterfront splits by &loc=, not by class-name token. An earlier pass
    # searched the class names for site codes, found only CLIFFSI, and wrongly
    # concluded the org did not cover Winchester Gardens or Home2 Suites EWR. It
    # does -- their codes are simply not in the class names. All three loc codes
    # below were read off each pool page's find-a-lesson/?pool_id= link, and each
    # returns classes only on that pool's published open days. loc=cliffsi also
    # returns 115 classes against the name_code match's 114, so it is the more
    # complete split.
    dict(brand="British", org="530108", loc="cliffsi", slug="bss_cliffside_park",
         location="Cliffside Park, NJ", lma="British Swim School - Cliffside Park",
         note="700 Palisadium Dr. Open Sun/Mon/Wed/Fri; feed returns exactly those days."),
    dict(brand="British", org="530108", loc="SHMAPLE", slug="bss_winchester_maplewood",
         location="Maplewood, NJ", lma="British Swim School - Winchester Gardens Maplewood",
         note="333 Elmwood Avenue, Maplewood. Published hours Sun 9-2, Wed 4-8:30, "
              "Fri 4-8:30, closed Mon/Tue/Thu/Sat; the feed returns 77 classes on "
              "Sun 27, Fri 26, Wed 24 and nothing on the closed days."),
    dict(brand="British", org="530108", loc="newhot", slug="bss_home2_ewr",
         location="Newark, NJ", lma="British Swim School - Home2 Suites Newark Route 1&9",
         note="Home2 Suites EWR Airport -- Route 1&9 is the EWR frontage road. "
              "Published hours Sun 9-2, Thu 4-9, Fri 4-9, Sat 9-2, closed "
              "Mon/Tue/Wed; the feed returns 57 classes on Sun 16, Sat 16, Thu 18, "
              "Fri 7 and nothing on the closed days."),

    dict(brand="British", org="534305", name_code=["LivRite"], slug="bss_livrite_fishers",
         location="Fishers, IN", lma="British Swim School - LivRite Fitness Fishers",
         note="The org carries ONE 'LivRite' token while the central-indiana territory "
              "lists two LivRite pools, so the join was ambiguous until 2026-09-01. "
              "Resolved on schedule: North Indianapolis runs SUNDAYS ONLY (pool closed "
              "Mon-Sat) and every LivRite class in the feed is Tue, Thu or Sat, inside "
              "Fishers' own Tue/Thu 4-7pm and Sat 9:45am-2:15pm windows. All of them "
              "are Fishers."),
    dict(brand="British", org="515732", name_code=["BSSPINES"], slug="bss_pembroke_pines",
         location="Pembroke Pines, FL", lma="British Swim School - Pembroke Pines",
         note="BSSPINES carries 75 of the org's 257 classes. The org's other tokens are "
              "LADORAL (97), LAMIAG (33) and LAHIA (28); the territory lists five pools "
              "against four tokens, so North Miami Beach stays UNRESOLVED -- do not "
              "assume LAMIAG is it."),

    dict(brand="British", org="515732", name_code=["LAMIAG"], slug="bss_north_miami_beach",
         location="Miami, FL", lma="British Swim School - LA Fitness North Miami Beach",
         note="LAMIAG resolved 2026-09-01 on two independent signals, not on the "
              "abbreviation. The pool's address is 1580 NE MIAMI GARDENS Dr, which is "
              "what the token spells; and its published hours are Wed 4-8:30, Fri 4-8, "
              "Sat 9-1, Sun 9-1:30 with Mon/Tue/Thu closed, against the token's class "
              "days of Wed 12, Sat 8, Sun 8, Fri 5 and none on Mon/Tue/Thu. The org's "
              "sibling token LAHIAG is Hialeah Gardens. The territory also lists a "
              "Kendall pool that has NO token in the feed at all."),

    dict(brand="British", org="545910", name_code=["HTCYCOL"], slug="bss_collegeville",
         location="Collegeville, PA", lma="British Swim School - Courtyard Marriott Collegeville",
         note="Org found 2026-09-01 on the POOL page, not the territory page, and in a "
              "third URL form -- portal/ppLogin.asp?id=545910, alongside the known "
              "ParentPortal/Login?orgId= and regv2.asp?id= patterns. Match confirmed on "
              "schedule: HTCYCOL runs Mon 14 and Wed 17 classes and nothing else, "
              "against published pool hours of Mon & Wed 4:30-8pm with every other day "
              "closed. The org's other token, LAFPOT, runs Tue/Thu/Sat/Sun as well.",),

    dict(brand="British", org="543485", name_code=["PROVHS"], slug="bss_pineville_matthews",
         location="Charlotte, NC", lma="British Swim School - Pineville Matthews Charlotte",
         note="The org was already live for Ballantyne and Matthews; this site sat "
              "unsized on a suspected duplicate. It is NOT a duplicate. PROVHS is "
              "Matthews/Arboretum Providence HS at 1800 Pineville Matthews Rd, an exact "
              "match to our geo source, and all 15 of its classes run Sunday 4:00-7:00pm "
              "against published hours of Sunday only 4-7pm. The already-measured "
              "HIEMAT (Matthews, Holiday Inn Express, 9420 E Independence Blvd) runs "
              "Tuesday only, so the two cannot be conflated."),

    dict(brand="British", org="549004", name_code=None, slug="bss_allen_fairview",
         location="McKinney, TX", lma="British Swim School - 24 HR Fitness Allen/Fairview",
         note="Our row is LABELLED Allen/Fairview but its address, 1601 N Hardin Blvd "
              "McKinney TX 75071, is an exact match to the brand's 24 HR Fitness "
              "McKinney page -- same pool, different label. Whole-org: the "
              "mckinney-allen territory lists one pool and all 150 classes read "
              "'24Hr Fit'. Days confirm it: Tue/Thu/Fri/Sat/Sun carry classes and "
              "Mon/Wed carry none, against published hours that close Mon and Wed."),

    # Austin is the SECOND org with no code in its class names -- they read
    # "Adult 1 (3) - 10:30 a.m." with no site marker at all -- so like
    # Pittsburgh it must be split with &loc=. The code came off the pool page's
    # Take Swim Assessment link, find-a-lesson/?pool_id=24-NW, which is the
    # documented way to read one rather than guess it.
    dict(brand="British", org="529039", loc="24-NW", slug="bss_austin_nw",
         location="Austin, TX", lma="British Swim School - 24 Hour Fitness Austin NW",
         note="10616 Research Blvd Austin TX 78759, an exact match to our geo source. "
              "Published hours Mon 5-8pm, Thu 5-8pm, Sat 9:15am-2pm, closed "
              "Tue/Wed/Fri/Sun."),

    # --- British Pittsburgh: the one org with NO code in its class names -----
    # Class names here are bare ("Adult - 6:30pm"), so this org must be split
    # with &loc= like Bear Paddle. Codes are 6-char abbreviations and are NOT
    # guessable -- Wexford is WOOLND, with the letters transposed from the
    # obvious WOODLN. See _meta.pool_id_discovery in jr_orgs.json for the
    # reliable way to read them off British's own site.
    dict(brand="British", org="517761", loc="WOOLND", slug="bss_wexford",
         location="Wexford, PA", lma="British Swim School - Woodlands Foundation Wexford",
         note="Feeds the Wexford vs Cranberry competitive brief."),
    dict(brand="British", org="517761", loc="STBARN", slug="bss_valencia",
         location="Valencia, PA",
         lma="British Swim School - St. Barnabas Crystal Conservatories Valencia"),
    dict(brand="British", org="517761", loc="WILDWD", slug="bss_gibsonia",
         location="Gibsonia, PA", lma="British Swim School - Wildwood Hampton Township"),
]

# Brand-published instructor ratios. British's own pool pages publish these
# directly (e.g. /pittsburgh/location/woodlands-foundation-wexford/):
#   Group 1:4 or 1:6 | Semi-Private 1:2 | Private 1:1 | Barracuda swim team 8
# Tadpole / Stroke Development are the 1:6 group; Barracuda is a swim team and
# is much larger, which is what produced the odd max_open=13 seen during
# calibration. No public ratio was found for Bear Paddle, so it falls through
# to observed-max only.
PUBLISHED_RATIO = {"British": {"_default": 4, "Tadpole": 6, "Barracuda": 8}}

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

    # Find the header by locating the cell that says "Class", wherever it sits.
    # Do NOT key off cell 0: on orgs that expose a Register column, cell 0 reads
    # "Register" on the header AND on every enrollable data row, so matching it
    # silently drops every class with openings and yields a fake 100%. But cell
    # 1 is not safe either -- orgs with public registration switched off emit no
    # Register column at all and put Class at index 0, which returned zero rows
    # for every Swim Atlanta org until 2026-08-31. Find the index, then reuse it.
    hdr = class_ix = None
    for r in rows:
        for i, c in enumerate(r[:2]):
            if c.strip().lower() == "class":
                hdr = [x.strip().lower().replace(" ", "_") for x in r]
                class_ix = i
                break
        if hdr:
            break
    if hdr is None:
        return []
    out = []
    for r in rows:
        if len(r) != len(hdr):
            continue
        if r[class_ix].strip().lower() == "class":
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
        elif not s.get("name_code"):
            # Whole-org site: the operator runs one Jackrabbit org per physical
            # location (Swim Atlanta, Barron), so the org feed IS the site and
            # there is nothing to split on. Distinguished from a missing code by
            # being explicit -- name_code=None, never omitted by accident.
            rows = list(orgcache[s["org"]])
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
