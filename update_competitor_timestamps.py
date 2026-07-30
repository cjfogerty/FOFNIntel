#!/usr/bin/env python3
"""
Stamp each Competitive Intel card on index.html with a "Last updated" date --
the date of that page's last git commit, since these are static generated
pages that only change when actually refreshed. Idempotent: safe to re-run
any time a competitor page is rebuilt (replaces its own previous stamp rather
than duplicating).

The refresh-barron.yml pipeline (Barron + Bear Paddle, both Jackrabbit-based)
calls this automatically after each refresh. The iClass Pro competitors
(coswimschool/littlekickers/tsswim) aren't on an automated schedule -- re-run
this by hand after manually rebuilding one of those with
build_iclass_dashboard.py.

Usage: python3 update_competitor_timestamps.py [--pull-date YYYY-MM-DD]

--pull-date is for the CI pipeline: it's called in the same run that just
wrote the freshly-pulled htmls, before they're committed, so `git log` would
still report the PREVIOUS commit's date. Pass the pull date the workflow
already computed instead of falling back to git log for those files.
"""
import argparse
import datetime
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(ROOT, "index.html")

# Manually-rebuilt iClass Pro competitors -- always use git log, no override.
SIMPLE_CARDS = ["coswimschool.html", "littlekickers.html", "tsswim.html"]
# Simple <a class="card competitor"> cards that ARE on the CI pipeline -- get
# --pull-date applied since git log would report last run's date, not this one.
AUTO_SIMPLE_CARDS = ["bearpaddle_bloomingdale.html"]
BARRON_LOCATIONS = ["barron_ofallon.html", "barron_ballwin.html", "barron_southcounty.html"]


def last_updated(fname):
    path = os.path.join(ROOT, fname)
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", fname],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if out:
            return out
    except subprocess.CalledProcessError:
        pass
    if os.path.exists(path):
        return datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    return None


def stamp_simple_card(html, fname, override_date=None):
    date = override_date or last_updated(fname)
    if not date:
        return html
    pattern = re.compile(
        r'(<a class="card competitor" href="' + re.escape(fname) + r'">.*?)'
        r'(<span class="last-updated"[^>]*>.*?</span>\s*)?(</a>)',
        re.S,
    )

    def repl(m):
        return (m.group(1)
                + f'<span class="last-updated" data-file="{fname}">Last updated: {date}</span>\n      '
                + m.group(3))

    new_html, n = pattern.subn(repl, html, count=1)
    if n == 0:
        print(f"warning: no card found for {fname}")
    return new_html


def stamp_barron_link(html, fname, override_date=None):
    date = override_date or last_updated(fname)
    if not date:
        return html
    pattern = re.compile(
        r'(<a href="' + re.escape(fname) + r'"><span class="loc-name">[^<]*)'
        r'(<span class="last-updated"[^>]*>.*?</span>)?(</span>)',
        re.S,
    )

    def repl(m):
        return m.group(1).rstrip() + f' <span class="last-updated" data-file="{fname}">{date}</span>' + m.group(3)

    new_html, n = pattern.subn(repl, html, count=1)
    if n == 0:
        print(f"warning: no sub-link found for {fname}")
    return new_html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull-date", default=None)
    ap.add_argument("--barron-date", default=None, help=argparse.SUPPRESS)  # old name, kept working
    a = ap.parse_args()
    pull_date = a.pull_date or a.barron_date

    with open(INDEX) as f:
        html = f.read()
    for fname in SIMPLE_CARDS:
        html = stamp_simple_card(html, fname)
    for fname in AUTO_SIMPLE_CARDS:
        html = stamp_simple_card(html, fname, override_date=pull_date)
    for fname in BARRON_LOCATIONS:
        html = stamp_barron_link(html, fname, override_date=pull_date)
    with open(INDEX, "w") as f:
        f.write(html)
    total = len(SIMPLE_CARDS) + len(AUTO_SIMPLE_CARDS) + len(BARRON_LOCATIONS)
    print("stamped timestamps for", total, "cards")


if __name__ == "__main__":
    main()
